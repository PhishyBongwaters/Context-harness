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
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .assembly import assemble, load_prompt, write_assembled
from .context import (Budget, ContextFile, _split_atem_xml, count_tokens,
                      diff_transcripts, parse_transcript, render_assistant,
                      render_history_user, render_tool,
                      sanitize_assistant_content)
from .session import next_turn
from .approvals import (EXEC_TIMEOUT_DEFAULT, EXEC_TIMEOUT_MAX, Approver,
                       Policy, clamp_exec_timeout)
from .deterministic import (DEFAULT_KEEP_RECENT_TOOLS, DEFAULT_SECTION_CAP,
                            prune_deterministic)
from .providers import Provider, ProviderError
from .usage import UsageTracker
from .tools import _resolve, run_tool, tool_definitions

MAX_STEPS = 50
MAX_PRUNE_ATTEMPTS = 5
MAX_PRUNE_STEPS = 12

SYSTEM_PROMPT = """You are an agent running inside a context-as-file harness.

Each turn the harness assembles your context from sources, in order:
satellite files (facts, decisions, tasks -- durable one-liners),
history.md (the conversation record), and scratch.md (this episode's
working notes: recent replies and tool results). There is no other
memory. The assembled text is written to {ctx_path} for reference, but
it is rebuilt every turn -- do not try to edit it.

CURATION (your standing permission -- edits here never need approval):
you curate the durable sources with your edit tool (exact old_text /
new_text, which must match exactly once):
  history.md -- the conversation record; keep user messages, fold in
    what mattered from each turn
  sats/facts.md, sats/decisions.md, sats/tasks.md -- one-line facts,
    decisions with reasons, open tasks

Harness-owned files cannot be touched: prompt.md, state.json,
index.md, scratch.md, archive/, {ctx_path}. Write is rejected on all
session sources -- use edit.

EPISODES: your replies and tool results accumulate in the episode's
working notes. When you reply without calling tools, the episode
closes: the notes are archived and only a pointer remains in history.
Move anything durable into history.md or the sats BEFORE your closing
reply, or it leaves active context. You can re-read an archived
episode by its path when you truly need the detail.

TOOLS: Use the native function-calling tools provided by the API
(read, write, edit, exec, tokens). Call them directly -- do NOT emit
tool calls as text, XML, or JSON blocks in your reply.

IMPORTANT: After using tools, ALWAYS reply with a text summary of what
you did or found. Never end your turn with only tool calls and no text.
The user needs to see your response in the chat.

Never emit `## ` headers in your reply text -- the harness adds
structure when it records your reply.

BUDGET: hard limit {hard:,} tokens, soft warning at {soft:,} tokens.
The harness shows your usage every turn. If the next request would
exceed the hard limit, you do NOT get a normal turn -- you get a
curation turn where you may only edit history.md and the sats until
usage is back under the limit. The harness never silently truncates
your context.

APPROVALS: mutating tools (exec, write, edit outside your sources)
need human approval: the human may approve once, approve for the session,
or deny. A denied call returns a DENIED message -- respect it, do not
retry the same call, work another way or ask the user.
"""

PRUNE_SYSTEM = """You are over your context budget. This is a curation-only turn.

You may ONLY use the edit tool, and ONLY on these files:
  {history_path} -- the conversation record
  {sats_dir}/facts.md, {sats_dir}/decisions.md, {sats_dir}/tasks.md
    -- durable one-liners

Compress ruthlessly: summarize old history sections into fewer lines,
distill working notes into sat one-liners, drop what no longer serves
the task. Each edit needs old_text matching exactly once -- include
enough surrounding context. Keep the `## ` section format parseable.
Do not attempt the user's task now -- just curate.
Reply with one line (PRUNED) only after your edits have actually shrunk
the assembled context -- the harness re-measures, and an unchanged
assembly just repeats this turn. Replying PRUNED without editing
accomplishes nothing.
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
        # Blank-slate layout (T1): prompt, state, index, history, sats,
        # scratch, archive. Idempotent -- re-opening keeps everything.
        # context.md is the assembled artifact (T3/T6), not a transcript.
        from .session import init_layout
        init_layout(self.dir, SYSTEM_PROMPT)
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


class TurnInterrupted(Exception):
    """Cooperative turn cancellation via Loop.request_stop().

    Raised at safe points (between steps, before model calls and tool
    executions); never escapes run_turn -- it is caught there, an
    "interrupted" event is emitted, and the partial turn ends.
    """


class Loop:
    def __init__(self, provider: Provider, budget: Budget,
                 on_event=None, prune_provider: Provider | None = None,
                 approver: Approver | None = None,
                 exec_timeout: int = EXEC_TIMEOUT_DEFAULT,
                 exec_timeout_max: int = EXEC_TIMEOUT_MAX,
                 usage_tracker: UsageTracker | None = None,
                 usage_note: bool = True,
                 project: str | None = None,
                 prune_target: int | None = None,
                 prune_keep_tools: int = DEFAULT_KEEP_RECENT_TOOLS,
                 prune_section_cap: int = DEFAULT_SECTION_CAP):
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
        self.prune_target = prune_target  # None -> soft budget
        self.prune_keep_tools = prune_keep_tools
        self.prune_section_cap = prune_section_cap
        self._turn_seq = 0
        self._stop_event = threading.Event()
        self._tools = tool_definitions()
        # Curation turns are edit-only: write is denied on all
        # session sources by the gates (T5), so offering it would only
        # produce DENIED noise.
        self._prune_tools = [t for t in self._tools
                             if t["name"] in ("edit",)]

    def request_stop(self) -> None:
        """Ask a running turn to stop at the next safe point.

        Cooperative: a turn blocked inside a model call or tool
        execution finishes that call first (bounded by the request /
        exec timeouts), then aborts before storing or acting on the
        result. A completed turn always consumes the flag, so a stop
        can never leak into and kill the next turn.
        """
        self._stop_event.set()

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise TurnInterrupted()

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
                f"{self.budget.hard:,} budget -- only this-request tokens "
                f"count against budget. Session lifetime in {t['input']:,} "
                f"out {t['output']:,} is informational, not budget.]")
        return [{"role": "user", "content": "\n".join(lines)}]

    def _source_target(self, session: Session, name: str,
                         args: dict) -> Path | None:
        """Editable file for backup+diff, else None (T5).

        history/sat sources get pre-edit snapshots and mechanical
        diffs.
        """
        if name not in ("write", "edit"):
            return None
        try:
            from .tools import classify_source
            p = _resolve(args.get("path") or "", session.workdir)
            if classify_source(session.dir, p) in ("history", "sat"):
                return p
        except Exception:
            pass
        return None

    def _policy(self, session: Session) -> Policy:
        return Policy(session.workdir, session.dir, session.context.path)

    def _auto_dedupe(self, session: Session) -> None:
        """Run exact-dedupe on scratch.md after every tool result (T6).

        The harness does this mechanically -- byte-identical assistant/tool
        sections collapse to the newest. No model call, no budget check.
        """
        from .deterministic import dedupe_exact
        try:
            p = session.dir / "scratch.md"
            before = p.read_text(encoding="utf-8", errors="replace")
            after, dropped = dedupe_exact(before)
            if dropped and after != before:
                p.write_text(after, encoding="utf-8")
                self._emit("auto-dedupe", {"dropped": dropped})
        except Exception:
            pass

    @staticmethod
    def _append_source(session: Session, name: str, text: str) -> None:
        """Append to a session source file (history.md, scratch.md)."""
        p = session.dir / name
        cur = p.read_text(encoding="utf-8", errors="replace") \
            if p.exists() else ""
        if cur and not cur.endswith("\n"):
            cur += "\n"
        p.write_text(cur + text, encoding="utf-8")

    def _backup_context(self, session: Session, target: Path,
                        content: str, label: str):
        """Snapshot pre-edit source content (best effort).

        Prune turns label theirs `pre-prune` (historical name kept);
        ordinary model curation labels `pre-edit`. The backup carries
        the source file's stem. Collision-safe: same-second edits
        append a counter. Returns the path or None.
        """
        import datetime as _dt
        try:
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            base = target.with_name(
                f"{target.stem}.pre-{label}-{stamp}.bak")
            backup = base
            n = 1
            while backup.exists():
                backup = base.with_name(f"{base.stem}-{n}.bak")
                n += 1
            backup.write_text(content, encoding="utf-8")
            self._emit("backup", {"path": str(backup), "label": label})
            return backup
        except OSError:
            return None  # best effort: never block a turn on a backup

    def _close_episode(self, session: Session, turn_no: int) -> None:
        """Archive scratch.md, clear it, record the pointer (T7).

        Runs when the assistant replies with no more tool calls. The
        full episode trace moves to archive/<date>-t<NNNN>.md; history
        keeps a cheap `## episode t<NNNN>` pointer (archive path, turn
        range, tool-call count). Best effort: never block a turn.
        """
        import datetime as _dt
        import re as _re
        try:
            scratch_p = session.dir / "scratch.md"
            content = (scratch_p.read_text(encoding="utf-8",
                                           errors="replace")
                       if scratch_p.exists() else "")
            tool_calls = len(_re.findall(r"(?m)^## tool \S+", content))
            archive_dir = session.dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = _dt.datetime.now().strftime("%Y%m%d")
            dest = archive_dir / f"{stamp}-t{turn_no:04d}.md"
            n = 2
            while dest.exists():
                dest = archive_dir / f"{stamp}-t{turn_no:04d}-{n}.md"
                n += 1
            dest.write_text(content, encoding="utf-8")
            scratch_p.write_text("", encoding="utf-8")
            tag = f"t{turn_no:04d}"
            pointer = (f"## episode {tag}\n"
                       f"archive: archive/{dest.name}\n"
                       f"turns: {tag}-{tag}\n"
                       f"tool_calls: {tool_calls}\n")
            self._append_source(session, "history.md", pointer)
            # Refresh the inspectable artifact so it reflects the
            # closed episode, not the pre-close assembly.
            write_assembled(session.dir, assemble(session.dir))
            self._emit("episode-close", {"turn": turn_no,
                                         "archive": f"archive/{dest.name}",
                                         "tool_calls": tool_calls})
        except OSError:
            pass  # best effort: never block a turn on archiving

    def _execute_tool(self, session: Session, tc: dict) -> str:
        """Gate-checked, approval-gated tool run.

        Harness source gates (T5) run first: they are invariants, not
        user choices. Accepted edits to model-editable sources
        (history, sats) get a pre-edit backup and a mechanical diff.
        Denied calls return a DENIED message the model must respect.
        """
        from .tools import source_gate
        name, args = tc["name"], tc.get("arguments") or {}
        gate_denial = source_gate(session.dir, name,
                                  args.get("path") or "",
                                  session.workdir)
        if gate_denial is not None:
            self._emit("tool", {"name": name, "args": args,
                                "result": gate_denial,
                                "denied": True})
            return gate_denial
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
        target = self._source_target(session, name, args)
        before = (target.read_text(encoding="utf-8", errors="replace")
                  if target is not None else None)
        result = run_tool(name, args, session.workdir)
        if target is not None and before is not None:
            after = target.read_text(encoding="utf-8", errors="replace")
            if after != before:
                # The model curates its sources; the pre-edit snapshot
                # makes every such edit reversible.
                self._backup_context(session, target, before, "edit")
                self._emit("context-diff", diff_transcripts(before, after))
        self._emit("tool", {"name": name, "args": args, "result": result})
        return result

    def _transcript_messages(self, session: Session) -> tuple[str, list[dict]]:
        """Assemble the per-turn transcript from sources (T6).

        sats -> history -> scratch, written to context.md as an
        inspectable artifact, then parsed into messages.
        """
        raw = assemble(session.dir)
        write_assembled(session.dir, raw)
        return raw, parse_transcript(raw)

    def prune_turn(self, session: Session) -> bool:
        """Curation turns until under the hard budget (T6, spec section 7).

        Deterministic ladder first (on scratch.md), then the model gets
        edit-only curation turns on history.md/sats/*.md. The prune
        turn's own tool trace is ephemeral; only source edits persist.
        Loud on failure.
        """
        ctx_path = str(session.dir / "context.md")
        system = PRUNE_SYSTEM.format(
            history_path=str(session.dir / "history.md"),
            sats_dir=str(session.dir / "sats"),
            hard=self.budget.hard)
        scratch_p = session.dir / "scratch.md"
        self._backup_context(
            session, scratch_p,
            scratch_p.read_text(encoding="utf-8", errors="replace"),
            "prune")
        # Deterministic stages first: free, instant, no model calls. The
        # agent turn fires only if these didn't reach target.
        # T6: the ladder runs on scratch.md (the working trace is where
        # the bloat lives); history/sats are model-curated.
        det_before = scratch_p.read_text(encoding="utf-8",
                                         errors="replace")
        det_text, det_report = prune_deterministic(
            det_before,
            target=(self.prune_target if self.prune_target is not None
                    else self.budget.soft),
            keep_recent_tools=self.prune_keep_tools,
            section_cap=self.prune_section_cap,
            archive_dir=str(session.dir / "archive"))
        if det_text != det_before:
            scratch_p.write_text(det_text, encoding="utf-8")
            self._emit("context-diff", diff_transcripts(det_before, det_text))
            self._emit("prune-deterministic", det_report)
        # Gate on the EXACT next request: main system + full tools. Any
        # cheaper ruler (prune system, prune tools) reads under while the
        # main check stays over: prune declares victory without touching
        # the file and the loop burns all MAX_STEPS on identical OVER lines.
        main_system = load_prompt(session.dir, ctx_path=ctx_path,
                                  hard=self.budget.hard,
                                  soft=self.budget.soft)
        gate = lambda msgs: self._measure(
            self.provider, main_system, msgs, self._tools)["total"]
        for attempt in range(MAX_PRUNE_ATTEMPTS):
            self._check_stop()
            raw, messages = self._transcript_messages(session)
            if gate(messages) < self.budget.hard:
                return True
            self._emit("prune", {"attempt": attempt + 1,
                                 "tokens": gate(messages)})
            turn = [{"role": "user", "content": (
                f"Current assembled context "
                f"({gate(messages):,} tokens, hard limit "
                f"{self.budget.hard:,}):\n<context>\n{raw}\n</context>\n"
                f"Curate {session.dir / 'history.md'} and "
                f"{session.dir / 'sats'}/*.md with the edit tool until "
                f"the assembled context fits.")}]
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
                                       "window": self.budget.window,
                                       "usage_total": (
                                           self.tracker.totals
                                           if self.tracker else None),
                                       "messages": turn,
                                       "tools": [t["name"]
                                                 for t in self._prune_tools]})
                self._check_stop()
                resp = self.prune_provider.chat(
                    system=system, messages=turn + self._note(bd),
                    tools=self._prune_tools)
                # Same as main path: a stop requested mid-generation
                # takes effect before anything is stored or executed.
                self._check_stop()
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
                        f"No edits detected and the assembly is still "
                        f"{over_by:,} tokens over budget. Replying PRUNED "
                        f"without editing changes nothing. Use edit "
                        f"on the history/sats files now.")})
                    continue
                for tc in resp["tool_calls"]:
                    # No gate bypass: curation turns obey the same source
                    # gates as normal turns (spec section 7).
                    result = self._execute_tool(session, tc)
                    turn.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": result})
        raw, messages = self._transcript_messages(session)
        return gate(messages) < self.budget.hard

    def run_turn(self, session: Session, user_text: str) -> str:
        ctx_path = str(session.dir / "context.md")
        system = load_prompt(session.dir, ctx_path=ctx_path,
                             hard=self.budget.hard, soft=self.budget.soft)
        if self._stop_event.is_set():
            # Stop requested before the turn started: honor it.
            self._stop_event.clear()
            self._emit("interrupted", {})
            return ""
        if self.approver is not None:
            self.approver.new_turn()
        if self.tracker is not None and self.tracker.turns:
            self._turn_seq = max(t["turn"]
                                 for t in self.tracker.turns) + 1
        else:
            self._turn_seq += 1
        # The user's message joins history.md first, stamped with the
        # harness-owned turn counter (T6).
        turn_no = next_turn(session.dir)
        self._append_source(session, "history.md",
                            render_history_user(user_text, turn_no))
        warned = False

        try:
            for step in range(MAX_STEPS):
                self._check_stop()
                raw, messages = self._transcript_messages(session)
                if not messages:
                    raise ProviderError(
                        "Assembled context parses to zero messages -- the "
                        f"session sources in {session.dir} are empty or "
                        f"corrupt.")
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
                                       "window": self.budget.window,
                                       "usage_total": (
                                           self.tracker.totals
                                           if self.tracker else None),
                                       "messages": messages,
                                       "tools": [t["name"]
                                                 for t in self._tools]})
                self._check_stop()
                resp = self.provider.chat(system=system,
                                          messages=messages + self._note(bd),
                                          tools=self._tools)
                # The call above blocks (up to request_timeout): a stop
                # requested mid-generation must take effect here, before
                # the reply is stored or acted on — otherwise an Escape
                # during a tool-less reply is silently swallowed.
                self._check_stop()
                totals = self._track("main", step, bd, resp.get("usage"))
                self._emit("response", {"phase": "main",
                                        "content": resp.get("content"),
                                        "tool_calls": resp.get("tool_calls"),
                                        "usage": resp.get("usage"),
                                        "usage_total": totals})
                self._emit("usage", resp.get("usage") or {})
                tool_calls = resp.get("tool_calls") or []
                # Fallback: model emitted <atem:> XML instead of native tools.
                # Extract and execute it now, don't wait for transcript parse.
                content_raw = resp.get("content") or ""
                if not tool_calls and "<atem:" in content_raw:
                    _clean, _xml_calls = _split_atem_xml(content_raw)
                    if _xml_calls:
                        tool_calls = _xml_calls
                # Strip echoed transcript structure (## headers, tool-calls
                # fences) before storing or showing the reply.
                content = sanitize_assistant_content(resp.get("content"))
                # T6: the reply and tool results join scratch.md (the
                # episode's working notes), never the assembled
                # transcript.
                self._append_source(
                    session, "scratch.md",
                    render_assistant(content, tool_calls))
                if content:
                    self._emit("assistant", content)
                if not tool_calls:
                    # Normal completion consumes any pending stop (e.g. one
                    # that arrived after the last check): it must never
                    # leak into and kill the next turn.
                    self._stop_event.clear()
                    self._auto_dedupe(session)
                    # T7: the episode closes -- archive the trace, clear
                    # scratch, record the pointer.
                    self._close_episode(session, turn_no)
                    return content or ""
                for tc in tool_calls:
                    self._check_stop()
                    result = self._execute_tool(session, tc)
                    self._append_source(
                        session, "scratch.md",
                        render_tool(tc["id"], result))
                    self._auto_dedupe(session)
        except TurnInterrupted:
            self._stop_event.clear()
            self._emit("interrupted", {})
            return ""
        raise ProviderError(f"Turn exceeded {MAX_STEPS} steps without "
                            f"finishing.")
