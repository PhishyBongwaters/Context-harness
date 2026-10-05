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
from .usage import UsageTracker
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

CURATION (your transcript, your standing permission -- edits here never
need approval): keep ## user messages; summarize long ## assistant
replies instead of deleting them; prune stale ## tool results; keep
roughly the newest 15-20 turns in full. You are its curator, so prune
early and often -- do not wait for the budget to force it.

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
{hard:,} tokens. Strategy: keep all ## user messages; summarize (don't
just delete) long assistant replies; drop tool results no longer needed
for the task; keep the newest ~15-20 turns in full. Keep the `## user` /
`## assistant` / `## tool <id>` section format parseable, and do not
delete the most recent ## user section.
Do not attempt the user's task now -- just prune.
Reply with one line (PRUNED) only after your edits have actually shrunk
the file -- the harness re-measures, and an unchanged file just repeats
this turn. Replying PRUNED without editing accomplishes nothing.
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


def _estimate(system: str, messages: list[dict],
              tools: list[dict] | None = None) -> int:
    total = count_tokens(system)
    for m in messages:
        total += count_tokens(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += count_tokens(json.dumps(tc.get("arguments") or {}))
    for t in tools or []:
        # Tool schemas ride along on every request; count them too.
        total += count_tokens(json.dumps(t))
    return total


class Loop:
    def __init__(self, provider: Provider, budget: Budget,
                 on_event=None, prune_provider: Provider | None = None,
                 approver: Approver | None = None,
                 exec_timeout: int = EXEC_TIMEOUT_DEFAULT,
                 exec_timeout_max: int = EXEC_TIMEOUT_MAX,
                 usage_tracker: UsageTracker | None = None,
                 usage_note: bool = True,
                 project: str | None = None):
        self.provider = provider
        # Janitor model for prune-only turns; defaults to the main provider.
        self.prune_provider = prune_provider or provider
        self.budget = budget
        self.on_event = on_event or (lambda kind, data: None)
        self.approver = approver
        self.exec_timeout = exec_timeout
        self.exec_timeout_max = exec_timeout_max
        self.tracker = usage_tracker
        self.usage_note = usage_note
        self.project = project
        self._turn_seq = 0
        self._tools = tool_definitions()
        self._prune_tools = [t for t in self._tools
                             if t["name"] in ("write", "edit")]

    def _emit(self, kind: str, data):
        self.on_event(kind, data)

    def _measure(self, provider: Provider, system: str,
                   messages: list[dict], tools: list[dict]) -> dict:
        """Break the next request into measurable parts.

        system: harness instructions. transcript: the wire-format messages
        (translated when the provider offers it, so JSON envelope keys
        count). tools: the schemas riding along. total drives the meter
        and the budget; the wire template on the server side remains a
        small unmeasured margin.
        """
        translate = getattr(provider, "translate_messages", None)
        wire = translate(messages) if callable(translate) else messages
        parts = {"system": count_tokens(system),
                 "transcript": count_tokens(json.dumps(wire)),
                 "tools": sum(count_tokens(json.dumps(t)) for t in tools)}
        parts["total"] = parts["system"] + parts["transcript"] + parts["tools"]
        return parts

    def _track(self, phase: str, step: int, breakdown: dict,
               server: dict | None) -> dict:
        """Record one model call; returns running session totals."""
        if self.tracker is None:
            return {"input": 0, "output": 0, "estimated": 0}
        return self.tracker.record(turn=self._turn_seq, phase=phase,
                                   step=step, breakdown=breakdown,
                                   server=server)

    def _note(self, breakdown: dict) -> list[dict]:
        """Ephemeral usage line: sent to the model, never stored.

        The meter is console-only, so without this the model reasons
        from stale numbers pasted into its transcript. ~30 tokens.
        The project line rides along when a project is active.
        """
        if not self.usage_note and not self.project:
            return []
        t = self.tracker.totals if self.tracker else {"input": 0,
                                                      "output": 0}
        lines = []
        if self.project:
            lines.append(f"[project: {self.project}]")
        if self.usage_note:
            lines.append(
                f"[harness note: this request ≈ {breakdown['total']:,} "
                f"tokens (sys {breakdown['system']:,} + "
                f"chat {breakdown['transcript']:,} + "
                f"tools {breakdown['tools']:,}) of "
                f"{self.budget.hard:,} budget; session lifetime in "
                f"{t['input']:,} out {t['output']:,}]")
        return [{"role": "user", "content": "\n".join(lines)}]

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
        try:
            import datetime as _dt
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = session.context.path.with_name(
                f"context.pre-prune-{stamp}.bak")
            backup.write_text(session.context.load(), encoding="utf-8")
            self._emit("backup", {"path": str(backup)})
        except OSError:
            pass  # best effort: never block the prune on a backup
        # Gate on the EXACT next request: main system + full tools. Any
        # cheaper ruler (prune system, prune tools) reads under while the
        # main check stays over: prune declares victory without touching
        # the file and the loop burns all MAX_STEPS on identical OVER lines.
        main_system = SYSTEM_PROMPT.format(
            ctx_path=ctx_path, hard=self.budget.hard, soft=self.budget.soft)
        gate = lambda msgs: self._measure(
            self.provider, main_system, msgs, self._tools)["total"]
        for attempt in range(MAX_PRUNE_ATTEMPTS):
            raw, messages = self._transcript_messages(session)
            if gate(messages) < self.budget.hard:
                return True
            self._emit("prune", {"attempt": attempt + 1,
                                 "tokens": gate(messages)})
            turn = [{"role": "user", "content": (
                f"Current context file "
                f"({gate(messages):,} tokens, hard limit "
                f"{self.budget.hard:,}):\n<context-file>\n{raw}\n"
                f"</context-file>")}]
            for _step in range(MAX_PRUNE_STEPS):
                bd = self._measure(self.prune_provider, system, turn,
                                   self._prune_tools)
                self._emit("request", {"phase": "prune", "attempt": attempt + 1,
                                       "tokens_est": bd["total"],
                                       "breakdown": bd,
                                       "status": self.budget.status(
                                           bd["total"]),
                                       "hard": self.budget.hard,
                                       "soft": self.budget.soft,
                                       "usage_total": (
                                           self.tracker.totals
                                           if self.tracker else None),
                                       "messages": turn,
                                       "tools": [t["name"]
                                                 for t in self._prune_tools]})
                resp = self.prune_provider.chat(
                    system=system, messages=turn + self._note(bd),
                    tools=self._prune_tools)
                totals = self._track("prune", attempt, bd,
                                     resp.get("usage"))
                self._emit("response", {"phase": "prune",
                                        "content": resp.get("content"),
                                        "tool_calls": resp.get("tool_calls"),
                                        "usage": resp.get("usage"),
                                        "usage_total": totals})
                turn.append({"role": "assistant",
                             "content": resp.get("content"),
                             "tool_calls": resp.get("tool_calls")})
                if not resp.get("tool_calls"):
                    # A bare reply ends the round ONLY if the file is
                    # actually under budget -- otherwise the model has
                    # learned to escape by saying PRUNED. Nudge and retry
                    # within the same attempt instead of burning a whole
                    # multi-minute attempt on an unchanged file.
                    if gate(self._transcript_messages(session)[1]) < \
                            self.budget.hard:
                        break
                    over_by = gate(self._transcript_messages(session)[1]) - \
                        self.budget.hard
                    turn.append({"role": "user", "content": (
                        f"No edits detected and the file is still "
                        f"{over_by:,} tokens over budget. Replying PRUNED "
                        f"without editing changes nothing. Use write/edit "
                        f"on {ctx_path} now.")})
                    continue
                for tc in resp["tool_calls"]:
                    result = self._execute_tool(session, tc)
                    turn.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result})
        raw, messages = self._transcript_messages(session)
        return gate(messages) < self.budget.hard

    def run_turn(self, session: Session, user_text: str) -> str:
        ctx_path = str(session.context.path)
        system = SYSTEM_PROMPT.format(ctx_path=ctx_path,
                                     hard=self.budget.hard,
                                     soft=self.budget.soft)
        if self.approver is not None:
            self.approver.new_turn()
        if self.tracker is not None and self.tracker.turns:
            self._turn_seq = max(t["turn"]
                                 for t in self.tracker.turns) + 1
        else:
            self._turn_seq += 1
        # The user's message joins the file-transcript first.
        session.context.append(render_user(user_text))
        warned = False

        for step in range(MAX_STEPS):
            raw, messages = self._transcript_messages(session)
            if not messages:
                raise ProviderError(
                    "Context file parses to zero messages -- the transcript "
                    f"was emptied. Restore {ctx_path} and retry.")
            bd = self._measure(self.provider, system, messages, self._tools)
            est = bd["total"]
            status = self.budget.status(est)
            if status == "over":
                self._emit("budget", {"status": "over", "tokens": est,
                                      "breakdown": bd})
                if not self.prune_turn(session):
                    raise BudgetExceeded(
                        f"Still over hard budget ({self.budget.hard:,}) "
                        f"after {MAX_PRUNE_ATTEMPTS} prune attempts. "
                        f"Prune {ctx_path} by hand and retry.")
                warned = False
                continue
            if status == "warn" and not warned:
                warned = True
                self._emit("budget", {"status": "warn", "tokens": est,
                                      "breakdown": bd})

            self._emit("request", {"phase": "main", "step": step,
                                   "turn": self._turn_seq,
                                   "tokens_est": est, "status": status,
                                   "breakdown": bd,
                                   "hard": self.budget.hard,
                                   "soft": self.budget.soft,
                                   "usage_total": (
                                       self.tracker.totals
                                       if self.tracker else None),
                                   "messages": messages,
                                   "tools": [t["name"]
                                             for t in self._tools]})
            resp = self.provider.chat(system=system,
                                      messages=messages + self._note(bd),
                                      tools=self._tools)
            totals = self._track("main", step, bd, resp.get("usage"))
            self._emit("response", {"phase": "main",
                                    "content": resp.get("content"),
                                    "tool_calls": resp.get("tool_calls"),
                                    "usage": resp.get("usage"),
                                    "usage_total": totals})
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
