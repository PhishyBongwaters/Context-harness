"""Main agent loop: the context file IS the transcript (CLM-style).

Before every model call the harness reads context.md and parses it into
messages. After each response it appends the assistant section and tool
results to the file. The model restructures the file at any time with its
ordinary write/edit tools; the next read picks the edits up.

Budget enforcement:
  - soft breach -> warning event, turn continues
  - hard breach -> the model gets a prune-only turn (write/edit on the
    context file only, ephemeral transcript) until usage is back under the
    hard limit. The harness never silently truncates; if pruning fails
    after N attempts it raises loudly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .context import (Budget, ContextFile, count_tokens, diff_transcripts,
                      parse_transcript, render_assistant, render_tool,
                      render_user, sanitize_assistant_content)
from .approvals import (EXEC_TIMEOUT_DEFAULT, EXEC_TIMEOUT_MAX, Approver,
                       Policy, clamp_exec_timeout)
from .providers import Provider, ProviderError
from .tools import _resolve, run_tool, tool_definitions

MAX_STEPS = 50
MAX_PRUNE_ATTEMPTS = 5
MAX_PRUNE_STEPS = 12

SYSTEM_PROMPT = """You are an agent running inside a context-as-file harness.

The file {ctx_path} IS your live context -- the transcript itself. Before
every model call, the harness reads this file and sends it as the
conversation. There is no other memory. What you see each turn is exactly
this file.

FORMAT: sections start with a `## ` header, one per line:
  ## user                a user message
  ## assistant           one of your previous replies
  ## tool <id>           the result of a tool call
An assistant section may end with a ```tool-calls fenced JSON block listing
that reply's tool calls.

You can restructure this file freely with your write/edit tools: delete stale
sections, summarize old tool output in place, reorder, annotate. The parser
needs three things:
  - keep `## ` headers exactly as `## user`, `## assistant`, `## tool <id>`;
  - keep ```tool-calls blocks as valid JSON if you keep them;
  - a `## tool <id>` section is ignored unless an earlier assistant section
    lists that tool call id.

Never emit `## ` headers or ```tool-calls fences in your reply text --
the harness adds structure when it appends your reply. Echoing headers
back corrupts the transcript.

The harness appends new turns to the end of the file automatically (your
replies, tool results, user messages). Your edits apply on top.

BUDGET: hard limit {hard:,} tokens, soft warning at {soft:,} tokens.
The harness shows your usage every turn. If the next request would exceed
the hard limit, you do NOT get a normal turn -- you get a prune-only turn
where you may only edit {ctx_path} until usage is back under the limit.
The harness never silently truncates your transcript; you are its curator.

APPROVALS: mutating tools (exec, write, edit outside your transcript)
need human approval: the human may approve once, approve for the session,
or deny. A denied call returns a DENIED message -- respect it, do not
retry the same call, work another way or ask the user.
"""

PRUNE_SYSTEM = """You are over your context budget. This is a prune-only turn.

You may ONLY use the write/edit tools, and ONLY on {ctx_path}.
Rewrite, summarize, and cut until the transcript is comfortably under
{hard:,} tokens. Keep the `## user` / `## assistant` / `## tool <id>`
section format parseable, and do not delete the most recent ## user section.
Do not attempt the user's task now -- just prune.
When the file is under budget, reply with one line: PRUNED.
"""


class BudgetExceeded(Exception):
    pass


@dataclass
class Session:
    id: str
    dir: Path
    workdir: str
    context: ContextFile = field(init=False)

    def __post_init__(self):
        self.context = ContextFile(self.dir / "context.md")


def _estimate(system: str, messages: list[dict]) -> int:
    total = count_tokens(system)
    for m in messages:
        total += count_tokens(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += count_tokens(json.dumps(tc.get("arguments") or {}))
    return total


class Loop:
    def __init__(self, provider: Provider, budget: Budget,
                 on_event=None, prune_provider: Provider | None = None,
                 approver: Approver | None = None,
                 exec_timeout: int = EXEC_TIMEOUT_DEFAULT,
                 exec_timeout_max: int = EXEC_TIMEOUT_MAX):
        self.provider = provider
        # Janitor model for prune-only turns; defaults to the main provider.
        self.prune_provider = prune_provider or provider
        self.budget = budget
        self.on_event = on_event or (lambda kind, data: None)
        self.approver = approver
        self.exec_timeout = exec_timeout
        self.exec_timeout_max = exec_timeout_max
        self._tools = tool_definitions()
        self._prune_tools = [t for t in self._tools
                             if t["name"] in ("write", "edit")]

    def _emit(self, kind: str, data):
        self.on_event(kind, data)

    def _touches_context(self, session: Session, name: str,
                         args: dict) -> bool:
        if name not in ("write", "edit"):
            return False
        try:
            return (_resolve(args.get("path") or "", session.workdir)
                    == session.context.path)
        except Exception:
            return False

    def _policy(self, session: Session) -> Policy:
        return Policy(session.workdir, session.dir, session.context.path)

    def _execute_tool(self, session: Session, tc: dict) -> str:
        """Approval-gated tool run. If it edits the context file, emit a
        mechanical diff of what changed (sections + tokens recovered).
        Denied calls return a DENIED message the model must respect."""
        name, args = tc["name"], tc.get("arguments") or {}
        if self.approver is not None:
            ok, denial = self.approver.resolve(
                self._policy(session), name, args)
            if not ok:
                self._emit("tool", {"name": name, "args": args,
                                    "result": denial,
                                    "denied": True})
                return denial
        if name == "exec":
            args = clamp_exec_timeout(args, self.exec_timeout,
                                      self.exec_timeout_max)
        before = (session.context.load()
                  if self._touches_context(session, name, args) else None)
        result = run_tool(name, args, session.workdir)
        if before is not None:
            after = session.context.load()
            if after != before:
                self._emit("context-diff", diff_transcripts(before, after))
        self._emit("tool", {"name": name, "args": args, "result": result})
        return result

    def _transcript_messages(self, session: Session) -> tuple[str, list[dict]]:
        """Read the file, parse it, return (raw_text, messages)."""
        raw = session.context.load()
        return raw, parse_transcript(raw)

    def prune_turn(self, session: Session) -> bool:
        """Ephemeral prune-only turns until under the hard budget.

        Tool calls here are NOT appended to the file -- the prune turn's
        own transcript is ephemeral; only the model's edits to the file
        persist. Loud on failure.
        """
        ctx_path = str(session.context.path)
        system = PRUNE_SYSTEM.format(ctx_path=ctx_path, hard=self.budget.hard)
        for attempt in range(MAX_PRUNE_ATTEMPTS):
            raw, messages = self._transcript_messages(session)
            if _estimate(system, messages) < self.budget.hard:
                return True
            self._emit("prune", {"attempt": attempt + 1,
                                 "tokens": _estimate(system, messages)})
            turn = [{"role": "user", "content": (
                f"Current context file "
                f"({_estimate(system, messages):,} tokens, hard limit "
                f"{self.budget.hard:,}):\n<context-file>\n{raw}\n"
                f"</context-file>")}]
            for _ in range(MAX_PRUNE_STEPS):
                self._emit("request", {"phase": "prune", "attempt": attempt + 1,
                                       "messages": turn,
                                       "tools": [t["name"]
                                                 for t in self._prune_tools]})
                resp = self.prune_provider.chat(system=system, messages=turn,
                                                tools=self._prune_tools)
                self._emit("response", {"phase": "prune",
                                        "content": resp.get("content"),
                                        "tool_calls": resp.get("tool_calls"),
                                        "usage": resp.get("usage")})
                turn.append({"role": "assistant",
                             "content": resp.get("content"),
                             "tool_calls": resp.get("tool_calls")})
                if not resp.get("tool_calls"):
                    break
                for tc in resp["tool_calls"]:
                    result = self._execute_tool(session, tc)
                    turn.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result})
        raw, messages = self._transcript_messages(session)
        return _estimate(system, messages) < self.budget.hard

    def run_turn(self, session: Session, user_text: str) -> str:
        ctx_path = str(session.context.path)
        system = SYSTEM_PROMPT.format(ctx_path=ctx_path,
                                     hard=self.budget.hard,
                                     soft=self.budget.soft)
        if self.approver is not None:
            self.approver.new_turn()
        # The user's message joins the file-transcript first.
        session.context.append(render_user(user_text))
        warned = False

        for step in range(MAX_STEPS):
            raw, messages = self._transcript_messages(session)
            if not messages:
                raise ProviderError(
                    "Context file parses to zero messages -- the transcript "
                    f"was emptied. Restore {ctx_path} and retry.")
            est = _estimate(system, messages)
            status = self.budget.status(est)
            if status == "over":
                self._emit("budget", {"status": "over", "tokens": est})
                if not self.prune_turn(session):
                    raise BudgetExceeded(
                        f"Still over hard budget ({self.budget.hard:,}) "
                        f"after {MAX_PRUNE_ATTEMPTS} prune attempts. "
                        f"Prune {ctx_path} by hand and retry.")
                warned = False
                continue
            if status == "warn" and not warned:
                warned = True
                self._emit("budget", {"status": "warn", "tokens": est})

            self._emit("request", {"phase": "main", "step": step,
                                       "tokens_est": est, "status": status,
                                       "messages": messages,
                                       "tools": [t["name"]
                                                 for t in self._tools]})
            resp = self.provider.chat(system=system, messages=messages,
                                      tools=self._tools)
            self._emit("response", {"phase": "main",
                                    "content": resp.get("content"),
                                    "tool_calls": resp.get("tool_calls"),
                                    "usage": resp.get("usage")})
            self._emit("usage", resp.get("usage") or {})
            tool_calls = resp.get("tool_calls") or []
            # Strip echoed transcript structure (## headers, tool-calls
            # fences) before storing or showing the reply.
            content = sanitize_assistant_content(resp.get("content"))
            # The reply joins the file-transcript before tools run, so a
            # mid-turn edit of the file sees the reply already in place.
            session.context.append(
                render_assistant(content, tool_calls))
            if content:
                self._emit("assistant", content)
            if not tool_calls:
                return content or ""
            for tc in tool_calls:
                result = self._execute_tool(session, tc)
                session.context.append(render_tool(tc["id"], result))
        raise ProviderError(f"Turn exceeded {MAX_STEPS} steps without "
                            f"finishing.")
