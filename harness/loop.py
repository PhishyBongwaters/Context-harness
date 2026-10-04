"""Main agent loop: the context-as-file mechanics.

Each turn the model sees: system prompt + the context file + budget meter
+ the user's message. Tool results stream into the live turn transcript.

Budget enforcement:
  - soft breach -> warning line in the meter, turn continues
  - hard breach -> normal turn is NOT sent. Instead the model gets a
    prune-only turn (write/edit on the context file only) until usage is
    back under the hard limit. The harness never silently truncates;
    if pruning fails after N attempts it raises loudly.

The model curates context.md with the ordinary write/edit tools --
no special context tool exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .context import Budget, ContextFile, count_tokens
from .providers import Provider, ProviderError
from .tools import run_tool, tool_definitions

MAX_STEPS = 50
MAX_PRUNE_ATTEMPTS = 5
MAX_PRUNE_STEPS = 12

SYSTEM_PROMPT = """You are an agent running inside a context-as-file harness.

Your entire persistent memory is the file {ctx_path}. There is no hidden
transcript: when this turn ends, only what is written in that file survives
to the next turn. Read it at the start of every turn; update it as you work.

BUDGET: hard limit {hard:,} tokens, soft warning at {soft:,} tokens.
The harness shows your usage every turn. If the next request would exceed
the hard limit, you do NOT get a normal turn -- you get a prune-only turn
where you may only edit {ctx_path} until usage is back under the limit.
The harness never silently truncates your memory; you are its curator.

Your standing job: keep the file curated and compact -- goals, key facts,
decisions, where things stand. Summarize stale tool output, drop dead ends,
keep live threads organized. Write it for yourself: the you who reads it
next turn. At the end of each turn, fold anything worth keeping into the file.
"""

PRUNE_SYSTEM = """You are over your context budget. This is a prune-only turn.

You may ONLY use the write/edit tools, and ONLY on {ctx_path}.
Rewrite, summarize, and cut until the file is comfortably under {hard:,}
tokens. Do not attempt the user's task now -- just prune.
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


def _user_block(ctx_path: str, file_text: str, meter: str,
                user_text: str) -> dict:
    return {"role": "user", "content": (
        f"<context-file path=\"{ctx_path}\">\n{file_text}\n</context-file>\n\n"
        f"{meter}\n\n{user_text}")}


class Loop:
    def __init__(self, provider: Provider, budget: Budget,
                 on_event=None):
        self.provider = provider
        self.budget = budget
        self.on_event = on_event or (lambda kind, data: None)
        self._tools = tool_definitions()
        self._prune_tools = [t for t in self._tools
                             if t["name"] in ("write", "edit")]

    def _emit(self, kind: str, data):
        self.on_event(kind, data)

    def _run_tool_calls(self, session: Session, messages: list[dict],
                        tool_calls: list[dict], allowed: set[str] | None):
        for tc in tool_calls:
            name, args = tc["name"], tc.get("arguments") or {}
            if allowed is not None and name not in allowed:
                result = f"ERROR: tool '{name}' not allowed on a prune turn."
            else:
                result = run_tool(name, args, session.workdir)
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": result})
            self._emit("tool", {"name": name, "args": args, "result": result})

    def prune_turn(self, session: Session) -> bool:
        """Run prune-only turns until under the hard budget. Loud on failure."""
        ctx_path = str(session.context.path)
        for attempt in range(MAX_PRUNE_ATTEMPTS):
            file_text = session.context.load()
            est = count_tokens(file_text)
            if est < self.budget.hard:
                return True
            system = PRUNE_SYSTEM.format(ctx_path=ctx_path,
                                        hard=self.budget.hard)
            messages = [{"role": "user", "content": (
                f"Current file: {est:,} tokens (hard limit "
                f"{self.budget.hard:,}).\n<context-file>\n{file_text}\n"
                f"</context-file>")}]
            self._emit("prune", {"attempt": attempt + 1, "tokens": est})
            for _ in range(MAX_PRUNE_STEPS):
                resp = self.provider.chat(system=system, messages=messages,
                                          tools=self._prune_tools)
                messages.append({"role": "assistant",
                                 "content": resp.get("content"),
                                 "tool_calls": resp.get("tool_calls")})
                if not resp.get("tool_calls"):
                    break
                self._run_tool_calls(session, messages, resp["tool_calls"],
                                     allowed={"write", "edit"})
            # re-check after this prune attempt
            if count_tokens(session.context.load()) < self.budget.hard:
                return True
        return False

    def run_turn(self, session: Session, user_text: str) -> str:
        ctx_path = str(session.context.path)
        system = SYSTEM_PROMPT.format(ctx_path=ctx_path,
                                     hard=self.budget.hard,
                                     soft=self.budget.soft)
        warned = False
        messages = [_user_block(
            ctx_path, session.context.load(),
            self.budget.meter_line(count_tokens(session.context.load())),
            user_text)]

        for step in range(MAX_STEPS):
            est = _estimate(system, messages)
            status = self.budget.status(est)
            if status == "over":
                self._emit("budget", {"status": "over", "tokens": est})
                if not self.prune_turn(session):
                    raise BudgetExceeded(
                        f"Still over hard budget ({self.budget.hard:,}) "
                        f"after {MAX_PRUNE_ATTEMPTS} prune attempts. "
                        f"Prune {ctx_path} by hand and retry.")
                # fresh turn after pruning
                file_text = session.context.load()
                messages = [_user_block(
                    ctx_path, file_text,
                    self.budget.meter_line(count_tokens(file_text)),
                    user_text)]
                warned = False
                continue
            if status == "warn" and not warned:
                warned = True
                self._emit("budget", {"status": "warn", "tokens": est})

            resp = self.provider.chat(system=system, messages=messages,
                                      tools=self._tools)
            self._emit("usage", resp.get("usage") or {})
            messages.append({"role": "assistant", "content": resp.get("content"),
                             "tool_calls": resp.get("tool_calls")})
            if resp.get("content"):
                self._emit("assistant", resp["content"])
            if not resp.get("tool_calls"):
                return resp.get("content") or ""
            self._run_tool_calls(session, messages, resp["tool_calls"],
                                 allowed=None)
        raise ProviderError(f"Turn exceeded {MAX_STEPS} steps without "
                            f"finishing.")
