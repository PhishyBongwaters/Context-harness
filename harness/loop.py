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
                      render_history_auto, render_history_user, render_tool,
                      sanitize_assistant_content)
from .session import (GOALS_MAX_ATTEMPTS, GOALS_NUDGE, active_goals,
                      complete_goal, enforce_sats_budgets, goals_done,
                      next_turn, validate_goals)
from .approvals import (EXEC_TIMEOUT_DEFAULT, EXEC_TIMEOUT_MAX, Approver,
                       Policy, clamp_exec_timeout)
from .deterministic import (DEFAULT_KEEP_RECENT_TOOLS, DEFAULT_SECTION_CAP,
                            prune_deterministic)
from .providers import Provider, ProviderError
from .usage import UsageTracker
from .tools import _resolve, run_tool, tool_definitions
from .handoff import (HANDOFF_MAX_ATTEMPTS, HANDOFF_NUDGE,
                      HANDOFF_PROMPT_PARAGRAPH, HandoffLedger,
                      ensure_handoff_file, handoff_size, is_over_cap,
                      trim_handoff, validate_handoff)

MAX_STEPS = 50
# No-match search stall: this many consecutive searches returning
# "no matches" ends the turn and stops the chain. A model stuck
# re-searching with variants burns slow whole-tree walks; legitimate
# searches return matches and never trip it. Per-turn streak: it
# resets at turn start and on any other tool or matching search.
SEARCH_STALL_THRESHOLD = 3
# Repeats of the identical SUCCEEDING tool call (same name, same
# arguments) that end the turn. Failing calls never count -- they
# are the circuit breaker's job, and a failure resets the streak.
# The search stall above keeps its own, more specific message for
# fruitless searches; this one catches every other stuck loop --
# the same read, edit, or exec issued over and over with success.
# Per-turn streak: resets at turn start and on any different call
# or any failure. Denied/gated calls never reach the tail, so they
# neither count nor reset.
REPEAT_STALL_THRESHOLD = 3
# Per-file token bound measured on sats/*.md at turn end (except
# handoff.md, which has its own validation). Files over it are
# reported loudly, never truncated here -- curation belongs to the
# prune turn. Default matches prune_section_cap (8000) so the
# reporting bound equals what prune assembly keeps per section --
# but it is an independent knob: changing the prune cap does not
# move this one.
SATS_CAP_TOKENS = 8000
MAX_PRUNE_ATTEMPTS = 5
MAX_PRUNE_STEPS = 12

# Goal-driven auto-continue (docs/goal-auto-continue-spec.md): the
# synthesized turn-opening message for a harness-initiated turn.
# Recorded verbatim in history.md under `## auto tNNNN`.
# NOTE: %-style placeholders, not str.format -- the goal text is
# model/user-controlled and may contain curly braces; %-formatting
# only interprets the template, never the values.
AUTO_CONTINUE_TEMPLATE = (
    "[auto-continue %d/%d] Active goal: %s. "
    "Continue working it; your handoff's Next says where."
)

SYSTEM_PROMPT = """You are a helpful AI assistant running inside a context-as-file harness.
Answer the user directly and concisely; use tools only when the task
genuinely needs them. You are on {os_name}; your working directory is
{workdir}.

Tool choice: prefer the `search` tool for code search (it works on any
OS and needs no approval). On Windows, `grep` is not installed -- do
not `exec` it; `rg` (ripgrep) is available if you must shell out.

Each turn the harness assembles your context from sources, in order:
satellite files (handoff -- this turn's shift board -- plus current task,
goals, facts, decisions, tasks -- durable one-liners), history.md (the
conversation record), and
scratch.md (this episode's working notes: recent replies and tool
results). There is no other memory. The assembled text below IS your context -- it is also saved
to {ctx_path}, but never read that file: it duplicates what you can
already see, doubling your context for nothing.

CURATION (your standing permission -- edits here never need approval):
you curate the durable sources with your edit tool (exact old_text /
new_text, which must match exactly once):
  {sdir}/history.md -- the conversation record (the harness records every
    turn and archives old turns automatically; correct mistakes, don't
    manage size)
  {sdir}/sats/current.md -- what you are actively working on; update it
    when the task changes or completes
  {sdir}/sats/goals.md -- enduring objectives (`- [active]` / `- [done]`)
  {sdir}/sats/facts.md, {sdir}/sats/decisions.md, {sdir}/sats/tasks.md -- one-line facts,
    decisions with reasons, open tasks
  {sdir}/sats/hypotheses.md -- half-formed ideas, unproven suspicions (prune ruthlessly)

  Write live: update sats/current.md as you make progress, not just
  at turn end. If the turn is interrupted, current.md is what survives
  -- scratch is wiped and the handoff may never be written. A turn
  that dies with its progress only in scratch has lost that progress.

  The sats above are already IN your context this turn, assembled as
  `## sat <name>` sections. Never `read` a sat file to see its
  contents -- that burns a tool call and bloats scratch with a copy
  of what you can already see, exactly like reading context.md would.
  When a sat needs updating, `edit` it directly; you already know
  what it says.

  Heavy curation (large histories, many files to triage): prefer
  `delegate` -- a subagent does the reading and editing in its own
  context; you read back only its result file. The noise never enters
  your transcript. This is not laziness, it is the architecture: your
  active context is the scarce resource, and spending a fraction of
  budget on a subagent to keep it clean is the win. Never apologize
  for delegating; apologize for bloating your own context instead.

GOALS: a `- [active]` goal in sats/goals.md is a standing order, not a
note. While any goal is still [active], keep working it: make tool
calls toward it rather than ending the turn with a text-only reply.
The "reply with a text summary after tools" rule applies when no goal
is active -- with an active goal, the summary comes when the goal is
[done]. End the turn text-only when every goal is [done], when you are
blocked (record the blocker in the handoff and say so), or when you
need the user (a question, an approval). Mark [done] only when it is
actually done.
""" + HANDOFF_PROMPT_PARAGRAPH + """
Harness-owned files cannot be touched: prompt.md, state.json,
index.md, scratch.md, archive/, {ctx_path}. Write is rejected on all
session sources -- use edit. Read is rejected on {ctx_path}: the
assembled text is already your conversation below.

EPISODES: your replies and tool results accumulate in the episode's
working notes. When you reply without calling tools, the episode
closes: the notes are archived, your reply is recorded in history,
and old history is archived automatically the same way. Nothing you
need to do -- the harness handles it. You can re-read anything
archived by its path when you truly need the detail.

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
  {sats_dir}/current.md, {sats_dir}/goals.md, {sats_dir}/facts.md,
  {sats_dir}/decisions.md, {sats_dir}/tasks.md, {sats_dir}/handoff.md,
  {sats_dir}/hypotheses.md -- durable one-liners

History size is harness-managed (old turns archive automatically);
do not summarize it. If the sats have grown unbounded, trim them to
one-liners and drop what no longer serves the task. {sats_dir}/handoff.md
is the resume plan: keep its Now/Next/Blockers/Pointers sections and its
`## Handoff -- tNNNN` stamp -- trim the bodies, never the shape. Each edit needs
old_text matching exactly once -- include enough surrounding context.
Keep the `## ` section format parseable.
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


class SearchStall(Exception):
    """Consecutive searches returned no matches, threshold reached.

    Carries the patterns tried. Caught in _run_turn_once like an
    interrupt: the turn ends with a loud message and run_turn does
    not chain, so a stuck model cannot restart the loop next turn.
    """

    def __init__(self, patterns: list[str]):
        super().__init__("search stall")
        self.patterns = list(patterns)


class RepeatStall(Exception):
    """The same succeeding tool call repeated consecutively,
    threshold reached.

    Carries the tool name and the repeat count. Caught in
    _run_turn_once like SearchStall: the turn ends with a loud
    message and run_turn does not chain. Fires for any tool,
    including searches that DO match -- a model issuing the same
    call over and over is stuck whether the call succeeds or not.
    Failing calls never count: they are the circuit breaker's job
    (and a failure resets the streak). A no-match search streak
    still raises SearchStall first, so the fruitless-search
    message stays specific. Prune turns catch this per attempt
    instead of letting it escape (see prune_turn).
    """

    def __init__(self, name: str, count: int):
        super().__init__("repeat stall")
        self.name = name
        self.count = count


class TurnInterrupted(Exception):
    """Cooperative turn cancellation via Loop.request_stop().

    Raised at safe points (between steps, before model calls and tool
    executions); never escapes run_turn -- it is caught there, an
    "interrupted" event is emitted, and the partial turn ends.
    """


def _parse_delegate_model(spec: str,
                          default_provider: str) -> tuple[str, str]:
    """'provider/model' or bare 'model' -> (provider, model)."""
    spec = spec.strip()
    if "/" in spec:
        provider, model = spec.split("/", 1)
        return provider.strip(), model.strip()
    return default_provider, spec


def _redirect_sats_path(args: dict, session_dir) -> dict:
    """Mechanical sats redirect, ported from the Oct 16-17 main-line
    fix that never reached the session branch (it lived only in that
    build line and was lost in branch shuffling).

    The model anchors "sats/..." at its workdir, so a write/edit to a
    workdir sats/*.md path would otherwise go to approval (a workdir
    write), time out to deny, and loop -- the day-1 symptom, seen
    live again 2026-10-21 on session 20261021-080207. Rewrite any
    write/edit whose path has a `sats` directory segment and a `.md`
    filename to <session_dir>/sats/<filename> before any gate or
    approval runs: no prompt, no DENIED for the redirect itself. A
    path already inside the session sats dir maps to itself.
    Separator-aware (Windows backslashes included); a bare `sats.md`
    filename (no directory segment) is not rewritten.

    This only changes WHERE a sats write lands, never whether it is
    allowed: the redirected call still passes through source_gate
    (write to a session source stays edit-only, with guidance) and
    approval policy afterwards. Reads are deliberately untouched:
    reading a workdir sats file is repo analysis, not state curation.
    """
    path = (args or {}).get("path") or ""
    if not path:
        return args
    parts = [p for p in path.replace("\\", "/").split("/")
             if p not in ("", ".")]
    if len(parts) < 2:
        return args
    if not any(p.lower() == "sats" for p in parts[:-1]):
        return args
    filename = parts[-1]
    if not filename.lower().endswith(".md"):
        return args
    new_args = dict(args)
    new_args["path"] = str(Path(session_dir) / "sats" / filename)
    return new_args


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
                 prune_section_cap: int = DEFAULT_SECTION_CAP,
                 prune_enabled: bool = True,
                 transcript_bloat_tokens: int | None = None,
                 history_cap: int | None = None,
                 is_subagent: bool = False,
                 subagent_id: str | None = None,
                 tools_allowlist: list[str] | None = None,
                 subagent_budget_fraction: float = 0.25,
                 delegate_model: str | None = None,
                 provider_factory=None,
                 include_text: str | None = None,
                 include_subagents: bool = False,
                 user_tool_paths: list | None = None,
                 auto_continue: bool = False,
                 auto_continue_max_turns: int = 3,
                 search_stall_threshold: int = SEARCH_STALL_THRESHOLD,
                 repeat_stall_threshold: int = REPEAT_STALL_THRESHOLD,
                 sats_cap_tokens: int = SATS_CAP_TOKENS):
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
        self.prune_enabled = prune_enabled
        # Transcript-bloat gate threshold: explicit value only. The
        # effective threshold is the transcript_bloat_tokens property
        # below -- never silently the soft budget. (The first version
        # of this redesign defaulted to soft; that assumed the budget
        # system already encodes when a handoff/prune must happen. It
        # doesn't: the gate exists precisely to force handoff+prune
        # early, because a model one-shots and bloats past the point
        # where a prune turn can still load the whole transcript.)
        self._bloat_explicit = transcript_bloat_tokens
        # User include text: appended to system prompt under "# User includes".
        self.include_text = include_text
        self.include_subagents = include_subagents
        # Goal-driven auto-continue (docs/goal-auto-continue-spec.md):
        # when a turn ends with any `- [active]` goal, the harness
        # starts another full turn instead of waiting for input, up to
        # auto_continue_max_turns per user message. Default off.
        # Subagents never auto-continue (out of scope for v1).
        self.auto_continue = bool(auto_continue)
        try:
            self._auto_continue_max_turns = max(
                0, int(auto_continue_max_turns))
        except (TypeError, ValueError):
            self._auto_continue_max_turns = 0
        # H2: history token cap; oldest turns archive deterministically.
        # None -> half the soft budget.
        self.history_cap = (history_cap if history_cap is not None
                            else budget.soft // 2)
        self._turn_seq = 0
        self._stop_event = threading.Event()
        # No-match search stall state (per turn; reset at turn start).
        self._search_stall_threshold = search_stall_threshold
        self._no_match_streak = 0
        self._no_match_patterns: list[str] = []
        # Repeat stall state (per turn; reset at turn start).
        self._repeat_stall_threshold = repeat_stall_threshold
        self._repeat_streak = 0
        self._last_tool_sig: tuple | None = None
        # Sats budget cap, enforced at turn end.
        self._sats_cap_tokens = sats_cap_tokens
        self.is_subagent = is_subagent
        self.subagent_id = subagent_id
        self._subagent_budget_fraction = subagent_budget_fraction
        self._delegate_model = delegate_model
        self._provider_factory = provider_factory
        self._tools = tool_definitions(
            include_delegation=not is_subagent)
        # User tools: scripts as tools, loaded from manifests.
        self._user_executors: dict = {}
        if user_tool_paths:
            from .user_tools import load_user_tools
            defs, execs = load_user_tools(user_tool_paths)
            # Don't let user tools shadow built-ins.
            builtin_names = {t["name"] for t in self._tools}
            for d in defs:
                if d["name"] not in builtin_names:
                    self._tools.append(d)
            self._user_executors = {
                k: v for k, v in execs.items()
                if k not in builtin_names
            }
        if tools_allowlist is not None:
            allowed = set(tools_allowlist)
            self._tools = [t for t in self._tools
                           if t["name"] in allowed]
        # One subagent slot per parent loop (spec: one at a time).
        self._subagent: dict | None = None
        # Completed subagents, drained into history.md at episode close
        # so the model retains a durable record of what it delegated.
        self._completed_subagents: list[dict] = []
        # Circuit breaker: tool name -> consecutive denial/error
        # count. Stops the model spamming calls the harness keeps
        # refusing (keyed on tool, not args: varying the command
        # after a denial is still spamming).
        self._denial_counts: dict[str, int] = {}
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
        result. A stop is a run-level signal, not a turn-level one:
        if the turn already completed by the time the flag is seen,
        run_turn honors it before chaining, so a stop can never be
        silently eaten by a completing turn and leave the chain
        running. The flag is cleared whenever it is honored (step
        boundary, turn start, or between turns), so it never leaks
        into a later user turn.
        """
        self._stop_event.set()

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise TurnInterrupted()

    def _emit(self, kind: str, data):
        self.on_event(kind, data)

    @property
    def transcript_bloat_tokens(self) -> int:
        """Effective bloat-gate threshold, in transcript tokens.

        Resolution order: explicit config/CLI value wins; else 30% of
        the known context window (for a 135,168-token window that is
        40,550 -- the "prune at 40k so a prune turn can still load all
        of it" rule, with headroom for system, tools, and the prune
        turn's own output); else the soft budget when the window is
        unknown. Live property, not a stored value, so a model switch
        that re-detects the window moves the threshold with it.
        """
        if self._bloat_explicit is not None:
            return self._bloat_explicit
        if self.budget.window:
            return int(self.budget.window * 0.3)
        return self.budget.soft

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
        if name == "goal_done":
            # The goal file is a sat: its flip gets the same pre-edit
            # snapshot and mechanical diff as a model edit. Missing
            # file means no backup (complete_goal reports no_active).
            p = Path(session.dir) / "sats" / "goals.md"
            return p if p.exists() else None
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

    def _naive_truncate(self, session: Session) -> None:
        """Condition A: drop oldest sections until under hard budget.

        No dedupe, no archive, no model call. The 'stupid and lazy'
        baseline for A/B evaluation.
        """
        from .deterministic import _split_sections, _split_preamble, _render
        from .context import count_tokens
        try:
            text = session.context.load()
            pre, _ = _split_preamble(text)
            sections = _split_sections(text)
            # Drop from oldest until under hard. Keep at least 2 sections.
            while len(sections) > 2:
                if count_tokens(_render(sections, pre)) < self.budget.hard:
                    break
                sections.pop(0)
            session.context.save(_render(sections, pre))
            self._emit("naive-truncate", {"sections_kept": len(sections)})
        except Exception:
            pass

    def _auto_dedupe(self, session: Session) -> None:
        """Run exact-dedupe on scratch.md after every tool result (T6).

        The harness does this mechanically -- byte-identical assistant/tool
        sections collapse to the newest. No model call, no budget check.
        Skipped in naive mode (--no-prune).
        """
        if not self.prune_enabled:
            return
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

    def _enforce_sats_budgets(self, session: Session) -> None:
        """Turn-end sats hygiene + cap reporting (best effort, never
        fatal).

        Dedupe and checkbox repair run in code (see
        session.enforce_sats_budgets); over-cap files are reported
        loudly, never truncated here -- curation belongs to the
        prune turn. Every changed file gets its pre-enforcement
        content snapshotted first, so a rewrite is reversible like
        any other sats edit, and a "sats-cap" event names what
        changed or breached. Applies in naive mode too: hygiene and
        a loud cap signal are safety bounds, not curation judgment.
        Deliberately NOT run on user-interrupted turns: a stop
        freezes state.
        """
        try:
            report = enforce_sats_budgets(session.dir,
                                          self._sats_cap_tokens)
        except Exception as e:  # never let enforcement break a turn
            self._emit("sats-cap-error", {"error": str(e)})
            return
        sats = Path(session.dir) / "sats"
        for name, info in report.items():
            if info["changed"]:
                self._backup_context(session, sats / name,
                                     info["before"], "sats-cap")
            self._emit("sats-cap", {
                "file": name,
                "tokens_before": info["tokens_before"],
                "tokens_after": info["tokens_after"],
                "dropped_lines": info["dropped_lines"],
                "repaired": info["repaired"],
                "over_cap": info["over_cap"],
                "changed": info["changed"],
            })

    def _ensure_handoff(self, session: Session, turn_no: int,
                          system: str, messages: list[dict]) -> bool:
        """Validate sats/handoff.md at the turn boundary (issue #5).

        Freshness, shape and size are machine-checked. A stale, missing
        or malformed handoff triggers forced handoff rounds: the model
        gets the assembled context plus a nudge and edit-only tools,
        and must rewrite the handoff before the turn may end.

        Returns True when the handoff is valid. After
        HANDOFF_MAX_ATTEMPTS failed rounds it emits "handoff-invalid"
        and returns False -- the turn still closes. Loud, never
        blocking: a broken handoff must not wedge the session.

        Phase 2: when the only remaining failure is the size cap, the
        deterministic trim backstop fires instead of giving up; every
        valid handoff is recorded in the size ledger (the canary).
        """
        ok, reason = validate_handoff(session.dir, turn_no)
        if ok:
            self._record_handoff_ok(session, turn_no)
            return True
        turn_stamp = f"t{turn_no:04d}"
        handoff_path = str(session.dir / "sats" / "handoff.md")
        for attempt in range(HANDOFF_MAX_ATTEMPTS):
            self._check_stop()
            # Defensive: the model has exec, so the file can vanish
            # between validation and the forced round. Restore the
            # shape; the model still has to fill it.
            ensure_handoff_file(session.dir)
            nudge = HANDOFF_NUDGE.format(handoff_path=handoff_path,
                                         turn_stamp=turn_stamp,
                                         reason=reason)
            handoff_messages = (messages
                                + [{"role": "user", "content": nudge}])
            try:
                bd = self._measure(self.provider, system,
                                   handoff_messages, self._prune_tools)
                self._emit("request",
                           {"phase": "handoff",
                            "attempt": attempt + 1,
                            "tokens_est": bd["total"],
                            "breakdown": bd,
                            "hard": self.budget.hard,
                            "soft": self.budget.soft,
                            "window": self.budget.window,
                            "status": self.budget.status(bd["total"])})
                resp = self.provider.chat(
                    system=system,
                    messages=handoff_messages + self._note(bd),
                    tools=self._prune_tools)
            except Exception as e:  # provider blew up: count the
                # attempt, keep the turn closable.
                reason = (f"handoff round {attempt + 1} failed: "
                          f"{type(e).__name__}")
                continue
            self._check_stop()
            for tc in resp.get("tool_calls") or []:
                self._execute_tool(session, tc)
            ok, reason = validate_handoff(session.dir, turn_no)
            if ok:
                self._record_handoff_ok(session, turn_no)
                return True
        if is_over_cap(session.dir):
            # The model wouldn't trim: deterministic backstop.
            if self._trim_handoff_backstop(session, turn_no):
                ok, reason = validate_handoff(session.dir, turn_no)
                if ok:
                    self._record_handoff_ok(session, turn_no)
                    return True
        self._emit("handoff-invalid", {"turn": turn_no, "reason": reason})
        return False

    def _ensure_goals(self, session: Session, turn_no: int,
                      system: str, messages: list[dict]) -> bool:
        """Validate sats/goals.md at the turn boundary.

        Mirrors _ensure_handoff for goals: a malformed goals file
        triggers forced fix rounds (edit-only tools, the model
        rewrites the file), and after GOALS_MAX_ATTEMPTS failed
        rounds it emits "goals-invalid" and returns False -- the
        turn still closes. Loud, never blocking. A missing or
        goal-less file is valid, so goal-less sessions never enter
        rounds.
        """
        ok, reason = validate_goals(session.dir)
        if ok:
            return True
        goals_path = str(session.dir / "sats" / "goals.md")
        for attempt in range(GOALS_MAX_ATTEMPTS):
            self._check_stop()
            nudge = GOALS_NUDGE.format(goals_path=goals_path,
                                       reason=reason)
            goals_messages = (messages
                              + [{"role": "user", "content": nudge}])
            try:
                bd = self._measure(self.provider, system,
                                   goals_messages, self._prune_tools)
                self._emit("request",
                           {"phase": "goals",
                            "attempt": attempt + 1,
                            "tokens_est": bd["total"],
                            "breakdown": bd,
                            "hard": self.budget.hard,
                            "soft": self.budget.soft,
                            "window": self.budget.window,
                            "status": self.budget.status(bd["total"])})
                resp = self.provider.chat(
                    system=system,
                    messages=goals_messages + self._note(bd),
                    tools=self._prune_tools)
            except Exception as e:  # provider blew up: count the
                # attempt, keep the turn closable.
                reason = (f"goals round {attempt + 1} failed: "
                          f"{type(e).__name__}")
                continue
            self._check_stop()
            for tc in resp.get("tool_calls") or []:
                self._execute_tool(session, tc)
            ok, reason = validate_goals(session.dir)
            if ok:
                return True
        self._emit("goals-invalid", {"turn": turn_no, "reason": reason})
        return False

    def _record_handoff_ok(self, session: Session,
                           turn_no: int) -> None:
        """Ledger + events for a valid handoff (Phase 2 canary).

        Records per-turn size and checks the trend: a climbing handoff
        is the dumping signal, emitted as "handoff-bloat" while it is
        still under the cap.
        """
        lines, tokens = handoff_size(session.dir)
        self._emit("handoff", {"turn": turn_no, "lines": lines,
                               "tokens": tokens})
        ledger = HandoffLedger(session.dir)
        ledger.record(turn_no, lines, tokens)
        if ledger.trend() == "climbing":
            self._emit("handoff-bloat",
                       {"turn": turn_no, "lines": lines,
                        "tokens": tokens,
                        "note": ("handoff size is climbing -- the agent "
                                 "is dumping instead of curating")})

    def _trim_handoff_backstop(self, session: Session,
                               turn_no: int) -> bool:
        """Deterministic trim when the model won't get under the cap.

        The model had its forced rounds; the harness now trims
        mechanically (headers kept, bodies cut from the end, marker
        appended). The pre-trim content is backed up, and the trim is
        announced -- the harness never silently rewrites model-curated
        state.
        """
        target = session.dir / "sats" / "handoff.md"
        try:
            before = target.read_text(encoding="utf-8",
                                      errors="replace")
        except OSError:
            return False
        trimmed, cut = trim_handoff(before)
        if cut <= 0:
            return False
        self._backup_context(session, target, before, "handoff-trim")
        try:
            target.write_text(trimmed, encoding="utf-8")
        except OSError:
            return False
        self._emit("handoff-trimmed", {"turn": turn_no,
                                       "lines_cut": cut})
        return True

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

    def _close_episode(self, session: Session, turn_no: int,
                       reply: str = "") -> None:
        """Archive scratch.md, clear it, record the pointer (T7).

        Runs when the assistant replies with no more tool calls. The
        full episode trace moves to archive/<date>-t<NNNN>.md; history
        keeps a cheap `## episode t<NNNN>` pointer (archive path, turn
        range, tool-call count). H1: the closing reply is recorded to
        history deterministically (stamped) -- the model is never asked
        to decide what is durable. Then history is windowed (H2).
        Best effort: never block a turn.
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
            # Durable subagent record: the model must remember what it
            # delegated, or it concludes it fabricated the results.
            if self._completed_subagents:
                lines = ["subagents:"]
                for c in self._completed_subagents:
                    lines.append(
                        f"  - {c['id']}: \"{c['task']}\" [{c['model']}] -> "
                        f"{c['status']} ({c['result_path']})")
                pointer += "\n".join(lines) + "\n"
                self._completed_subagents.clear()
            self._append_source(session, "history.md", pointer)
            # H1: the closing reply joins history, stamped. Empty
            # replies record nothing.
            if reply.strip():
                self._append_source(
                    session, "history.md",
                    f"## assistant {tag}\n{reply.strip()}\n")
            # H2: deterministic history windowing.
            self._window_history(session)
            # Refresh the inspectable artifact so it reflects the
            # closed episode, not the pre-close assembly.
            write_assembled(session.dir, assemble(session.dir))
            self._emit("episode-close", {"turn": turn_no,
                                         "archive": f"archive/{dest.name}",
                                         "tool_calls": tool_calls})
        except OSError:
            pass  # best effort: never block a turn on archiving

    def _window_history(self, session: Session) -> None:
        """Deterministic history bounding (H2)."""
        from .deterministic import window_history
        try:
            rep = window_history(session.dir,
                                 cap_tokens=self.history_cap)
            if rep.get("windowed"):
                self._emit("history-window", rep)
        except OSError:
            pass  # best effort

    def _execute_tool(self, session: Session, tc: dict) -> str:
        """Gate-checked, approval-gated tool run.

        Harness source gates (T5) run first: they are invariants, not
        user choices. Accepted edits to model-editable sources
        (history, sats) get a pre-edit backup and a mechanical diff.
        Denied calls return a DENIED message the model must respect.
        """
        from .tools import source_gate
        name, args = tc["name"], tc.get("arguments") or {}
        # Mechanical sats redirect (see _redirect_sats_path): must run
        # before source_gate and approval, or a workdir-anchored sats
        # write asks, times out, denies, and loops.
        if name in ("write", "edit"):
            args = _redirect_sats_path(args, session.dir)

        def _failed(result: str) -> str:
            self._denial_counts[name] = self._denial_counts.get(name, 0) + 1
            return result

        def _done(result: str) -> str:
            # ERROR/DENIED results feed the circuit breaker; anything
            # else resets it.
            if (result.startswith("ERROR") or "DENIED" in result
                    or '"status": "error"' in result):
                return _failed(result)
            self._denial_counts.pop(name, None)
            return result

        # Circuit breaker: stop accepting a tool the harness keeps
        # refusing. The model must try something else, not spam.
        if self._denial_counts.get(name, 0) >= 3:
            msg = (f"CIRCUIT BREAKER: '{name}' has been denied or failed "
                   f"3 times in a row. Stop calling it -- try a different "
                   f"tool, or ask the user.")
            self._emit("tool", {"name": name, "args": args,
                                "result": msg, "denied": True})
            return msg
        if self.is_subagent:
            sub_denial = self._subagent_gate(session, name, args)
            if sub_denial is not None:
                self._emit("tool", {"name": name, "args": args,
                                    "result": sub_denial,
                                    "denied": True})
                return _failed(sub_denial)
        gate_denial = source_gate(session.dir, name,
                                  args.get("path") or "",
                                  session.workdir)
        if gate_denial is not None:
            self._emit("tool", {"name": name, "args": args,
                                "result": gate_denial,
                                "denied": True})
            return _failed(gate_denial)
        # Delegation tools are harness orchestration, not mutations:
        # spawning/waiting/killing a subagent never needs approval.
        # (Mutations inside the subagent still go through its approver.)
        # goal_done is the same kind of exemption: it only flips a
        # checkbox in sats/goals.md, and write/edit to sats are already
        # allowed as the model's own memory curation. Exempting it
        # changes nothing about who can complete a goal -- it only
        # makes the transition code-owned instead of markdown-edited.
        if (self.approver is not None and name not in
                ("delegate", "wait_subagent", "cancel_subagent",
                 "goal_done")):
            ok, denial = self.approver.resolve(
                self._policy(session), name, args,
                subagent_id=(self.subagent_id
                             if self.is_subagent else None))
            if not ok:
                self._emit("tool", {"name": name, "args": args,
                                    "result": denial,
                                    "denied": True})
                return _failed(denial)
        if name == "exec":
            args = clamp_exec_timeout(args, self.exec_timeout,
                                      self.exec_timeout_max)
        if name in ("delegate", "wait_subagent", "cancel_subagent"):
            result = self._dispatch_delegation(session, name, args)
            self._emit("tool", {"name": name, "args": args,
                                "result": result})
            return _done(result)
        target = self._source_target(session, name, args)
        before = (target.read_text(encoding="utf-8", errors="replace")
                  if target is not None else None)
        if name in self._user_executors:
            try:
                result = self._user_executors[name](args or {},
                                                   session.workdir)
            except Exception as e:  # never let a user tool crash the loop
                result = f"ERROR executing user tool {name}: {e}"
        elif name == "goal_done":
            result = self._complete_goal(session, args)
        else:
            result = run_tool(name, args, session.workdir)
        if target is not None and before is not None:
            after = target.read_text(encoding="utf-8", errors="replace")
            if after != before:
                # The model curates its sources; the pre-edit snapshot
                # makes every such edit reversible.
                self._backup_context(session, target, before, "edit")
                self._emit("context-diff", diff_transcripts(before, after))
        self._emit("tool", {"name": name, "args": args, "result": result})
        # No-match search stall: consecutive fruitless searches only.
        # Denied/gated searches return above and never reach here, so
        # they neither count nor reset the streak.
        if name == "search":
            if result.startswith("no matches for"):
                self._no_match_streak += 1
                self._no_match_patterns.append(args.get("pattern") or "")
                if (self._no_match_streak
                        >= self._search_stall_threshold):
                    raise SearchStall(self._no_match_patterns)
            else:
                self._no_match_streak = 0
                self._no_match_patterns = []
        else:
            self._no_match_streak = 0
            self._no_match_patterns = []
        # Repeat stall: the identical SUCCEEDING call, consecutively,
        # whatever the tool. Failed calls are the circuit breaker's
        # job, not the stall's: a model retrying a failing call
        # must hit the breaker (or the prune gate), not a stall
        # message -- so a failure resets the streak instead of
        # counting. The failure test mirrors _done's below.
        # The signature is name + canonical arguments (sorted keys,
        # so key order never masks a repeat; the call id is
        # deliberately excluded -- a retried call is still the same
        # call). Runs after the search block, so a fruitless-search
        # streak keeps its specific message.
        if (result.startswith("ERROR") or "DENIED" in result
                or '"status": "error"' in result):
            self._repeat_streak = 0
            self._last_tool_sig = None
        else:
            sig = (name, json.dumps(args or {}, sort_keys=True,
                                    default=str))
            if sig == self._last_tool_sig:
                self._repeat_streak += 1
            else:
                self._repeat_streak = 1
                self._last_tool_sig = sig
            if self._repeat_streak >= self._repeat_stall_threshold:
                raise RepeatStall(name, self._repeat_streak)
        return _done(result)

    def _complete_goal(self, session: Session, args: dict) -> str:
        """Run the goal_done tool: flip the checkbox in code.

        The result tells the model exactly what happened, including
        what to do when the call cannot complete (name one goal, or
        use an exact text). When no active goals remain, the chain
        already stops after this turn and the turn-end handoff is
        skipped -- both read goals.md state, so no extra flag here.
        A failure never crashes the loop: it returns as an ERROR
        result, like every other tool failure.
        """
        try:
            status, matched, remaining = complete_goal(
                session.dir, (args or {}).get("goal"))
        except Exception as e:  # never let a tool crash the loop
            return f"ERROR: goal_done failed: {e}"
        if status == "done":
            if remaining:
                return (f"Goal marked done: {matched}. "
                        f"Active goals remaining: "
                        f"{'; '.join(remaining)}.")
            return (f"Goal marked done: {matched}. "
                    f"No active goals remain -- the chain stops "
                    f"after this turn.")
        if status == "no_active":
            return "No active goals to complete."
        if status == "ambiguous":
            return ("Several goals are active; call goal_done again "
                    f"naming one: {'; '.join(remaining)}.")
        return (f"No active goal matches "
                f"'{(args or {}).get('goal')}'. Active goals: "
                f"{'; '.join(remaining)}.")

    # --- subagent delegation (one at a time) ---

    def _subagent_gate(self, session: Session, name: str,
                       args: dict) -> str | None:
        """Deny a subagent any access to the parent session dir outside
        its own subdir. The subagent's world: its own session dir +
        the workdir."""
        if name not in ("read", "write", "edit"):
            return None
        path = args.get("path") or ""
        if not path:
            return None
        from .tools import _resolve
        try:
            target = _resolve(path, session.workdir).resolve()
        except Exception:
            return None
        parent = session.dir.parent.parent.resolve()
        own = session.dir.resolve()
        try:
            target.relative_to(parent)
        except ValueError:
            return None  # not under the parent session dir: fine
        try:
            target.relative_to(own)
            return None  # inside the subagent's own dir: fine
        except ValueError:
            pass
        return ("DENIED: subagents cannot access the parent session "
                "directory.")

    def _dispatch_delegation(self, session: Session, name: str,
                             args: dict) -> str:
        import json as _j
        if self.is_subagent:
            return _j.dumps({
                "status": "error",
                "message": ("subagents cannot delegate: you have no "
                            "delegate tool; do the work yourself.")})
        if name == "delegate":
            return self._delegate(session, args)
        if name == "wait_subagent":
            return self._wait_subagent(args)
        if name == "cancel_subagent":
            return self._cancel_subagent()
        return f"ERROR: unknown delegation tool '{name}'"

    def _delegate(self, session: Session, args: dict) -> str:
        import json
        import threading
        task = (args.get("task") or "").strip()
        if not task:
            return json.dumps({"status": "error",
                               "message": "task is required"})
        cur = self._subagent
        if cur is not None and cur["thread"].is_alive():
            return json.dumps({
                "status": "error",
                "message": ("a subagent is already running -- wait for it "
                            "with wait_subagent or stop it with "
                            "cancel_subagent first"),
            })
        from .session import init_subagent_session
        from .context import Budget
        sub_hard = int(args.get("budget_hard") or
                       self.budget.hard * self._subagent_budget_fraction)
        sub_soft = int(sub_hard * 0.8)
        paths = init_subagent_session(session.dir, task)
        sdir, sid = paths["dir"], paths["id"]
        sub_session = Session(id=f"sub-{sid}", dir=sdir,
                              workdir=session.workdir)
        allowlist = args.get("tools") or ["read", "edit", "exec",
                                          "tokens", "list_dir",
                                          "search"]
        # Provider/model for the subagent: per-call model arg wins,
        # then the delegate_model config, else inherit the parent's.
        provider = self.provider
        model_spec = ((args.get("model") or "").strip()
                      or (self._delegate_model or "").strip())
        if model_spec and self._provider_factory is not None:
            prov_name, model_name = _parse_delegate_model(
                model_spec, self.provider.name)
            try:
                provider = self._provider_factory(provider=prov_name,
                                                  model=model_name)
            except Exception:
                pass  # fall back to the parent's provider
        sub_loop = Loop(
            provider=provider,
            budget=Budget(hard=sub_hard, soft=sub_soft,
                          window=self.budget.window),
            # Subagent internals stay out of the parent's UI: its
            # request/response/tool events go nowhere. The parent only
            # sees subagent-spawn / subagent-done (emitted below via
            # the parent's own on_event). Full audit trail lives in
            # the subagent's session dir.
            on_event=lambda kind, data: None,
            approver=self.approver,
            exec_timeout=self.exec_timeout,
            exec_timeout_max=self.exec_timeout_max,
            usage_tracker=None,  # independent metering
            usage_note=False,
            project=self.project,
            is_subagent=True,
            # An explicit bloat-gate pin is inherited; otherwise the
            # subagent derives from the same window as the parent.
            transcript_bloat_tokens=self._bloat_explicit,
            subagent_id=sid,
            tools_allowlist=allowlist,
            include_text=(self.include_text if self.include_subagents
                          else None),
        )
        result_path = str(sdir / "result.md")
        prov = getattr(sub_loop, "provider", None)
        model_label = (f"{getattr(prov, 'name', '?')}/"
                       f"{getattr(prov, 'model', '?')}")
        slot = {"thread": None, "sdir": sdir, "id": sid,
                "result_path": result_path, "loop": sub_loop,
                "cancelled": False, "task": task,
                "model": model_label}
        self._subagent = slot

        def _run():
            output, status, error = "", "completed", None
            try:
                output = sub_loop.run_turn(sub_session, task) or ""
            except Exception as e:  # never let a thread die silent
                status, error = "failed", f"{type(e).__name__}: {e}"
            if slot["cancelled"]:
                status = "cancelled"
            self._write_subagent_result(slot, status, output, error)
            self._emit("subagent-done",
                       {"id": sid, "status": status,
                        "result_path": result_path})

        thread = threading.Thread(target=_run, daemon=True,
                                  name=f"subagent-{sid}")
        slot["thread"] = thread
        thread.start()
        self._emit("subagent-spawn",
                   {"id": sid, "task": task[:120],
                    "result_path": result_path})
        return json.dumps({"status": "running",
                           "result_path": result_path,
                           "session_dir": str(sdir),
                           "model": slot["model"]})

    def _wait_subagent(self, args: dict) -> str:
        import json
        import time
        slot = self._subagent
        if slot is None:
            return json.dumps({"status": "error",
                               "message": "no subagent has been delegated"})
        timeout = args.get("timeout")
        timeout = 120 if timeout is None else int(timeout)
        thread = slot["thread"]
        if timeout <= 0:
            alive = thread.is_alive()
        else:
            deadline = time.monotonic() + timeout
            while thread.is_alive() and time.monotonic() < deadline:
                thread.join(timeout=0.5)
            alive = thread.is_alive()
        if alive:
            return json.dumps({"status": "running",
                               "result_path": slot["result_path"],
                               "session_dir": str(slot["sdir"]),
                               "model": slot.get("model", "?")})
        # Thread finished: result.md was written in its finally path.
        try:
            import re
            text = open(slot["result_path"],
                        encoding="utf-8").read()
            m = re.search(r"^- status: (\w+)", text, re.M)
            status = m.group(1) if m else "completed"
        except OSError:
            status = "completed"
        out = {"status": status, "result_path": slot["result_path"],
               "session_dir": str(slot["sdir"]),
               "model": slot.get("model", "?")}
        if status == "failed":
            out["error"] = ("see result.md; for the full trace read "
                            f"{slot['sdir']}/history.md")
        # Durable record: drained into history.md at episode close.
        self._completed_subagents.append({
            "id": slot["id"],
            "task": (slot["task"].splitlines() or [""])[0][:120],
            "model": slot.get("model", "?"),
            "status": status,
            "result_path": slot["result_path"],
            "session_dir": str(slot["sdir"]),
        })
        return json.dumps(out)

    def _cancel_subagent(self) -> str:
        import json
        slot = self._subagent
        if slot is None:
            return json.dumps({"status": "error",
                               "message": "no subagent has been delegated"})
        slot["cancelled"] = True
        slot["loop"].request_stop()
        return json.dumps({"status": "cancelled",
                           "result_path": slot["result_path"]})

    def _write_subagent_result(self, slot: dict, status: str,
                               output: str, error: str | None) -> None:
        import datetime
        task_first = (slot["task"].splitlines() or [""])[0][:120]
        lines = [
            f"# Subagent result — {slot['id']}",
            "",
            f"- task: {task_first}",
            f"- model: {slot.get('model', '?')}",
            f"- status: {status}",
            f"- ended: {datetime.datetime.now().astimezone().isoformat()}",
            "",
        ]
        if status == "failed":
            lines += ["## Error", "", error or "unknown error", ""]
        else:
            lines += ["## Findings", "",
                      output.strip() or "(no output)", ""]
        try:
            Path(slot["result_path"]).write_text(
                "\n".join(lines), encoding="utf-8")
        except OSError:
            pass

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
                                  soft=self.budget.soft,
                                  workdir=session.workdir,
                                  include_text=self.include_text)
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
                try:
                    for tc in resp["tool_calls"]:
                        # No gate bypass: curation turns obey the same source
                        # gates as normal turns (spec section 7).
                        result = self._execute_tool(session, tc)
                        turn.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": result})
                except RepeatStall:
                    # A stuck janitor fails this attempt fast; the
                    # next attempt retries, and after
                    # MAX_PRUNE_ATTEMPTS the caller raises
                    # BudgetExceeded. The stall must not escape
                    # prune_turn and preempt that outcome.
                    break
        raw, messages = self._transcript_messages(session)
        return gate(messages) < self.budget.hard

    def run_turn(self, session: Session, user_text: str) -> str:
        """Run one user turn, then auto-continue while goals are active.

        Goal-driven auto-continue
        (docs/goal-auto-continue-spec.md): when a turn ends with any
        `- [active]` goal in sats/goals.md and auto_continue is enabled,
        the harness starts another full turn instead of waiting for
        input, up to auto_continue_max_turns per user message. Each
        auto-turn is a complete turn (assembly, budget, model steps,
        handoff validation, episode close) recorded under
        `## auto tNNNN`. The chain stops on: no active goals, cap
        reached, user interrupt, or a stop arriving between turns.
        Returns the last turn's reply.
        """
        max_auto = (self._auto_continue_max_turns
                    if self.auto_continue else 0)
        text = user_text
        is_auto = False
        auto_index = 0
        content = ""
        while True:
            content, interrupted = self._run_turn_once(
                session, text, is_auto=is_auto)
            if interrupted:
                # User interrupt stops the whole chain, not just the turn.
                return content
            if self._stop_event.is_set():
                # Stop arrived between turns: honor it, don't chain.
                self._stop_event.clear()
                self._emit("interrupted", {})
                return content
            if auto_index >= max_auto:
                return content
            goals = active_goals(session.dir)
            if not goals:
                return content
            auto_index += 1
            is_auto = True
            # %-formatting: the goal text may contain braces; it must
            # never be interpreted as a format string.
            text = AUTO_CONTINUE_TEMPLATE % (auto_index, max_auto,
                                             goals[0])
            self._emit("auto-continue",
                       {"index": auto_index, "max_turns": max_auto,
                        "goal": goals[0]})

    def _run_turn_once(self, session: Session, user_text: str, *,
                       is_auto: bool = False) -> tuple[str, bool]:
        """Run a single turn. Returns (reply, interrupted).

        interrupted is True when the user stopped the turn; the caller
        (run_turn's auto-continue chain) must not start another turn.
        """
        # The harness-owned turn counter increments first: the handoff
        # the model writes this turn must carry this turn's stamp.
        turn_no = next_turn(session.dir)
        ctx_path = str(session.dir / "context.md")
        system = load_prompt(session.dir, ctx_path=ctx_path,
                             hard=self.budget.hard, soft=self.budget.soft,
                             workdir=session.workdir,
                             include_text=self.include_text,
                             turn_no=turn_no)
        # Enforce handoff continuity: previous turn must have left a fresh
        # handoff. Recovery runs with the real system prompt (load_prompt
        # reads prompt.md only, it does not assemble the transcript, so
        # loading it first is safe). A broken handoff must not wedge the
        # session: recovery failure is loud but the turn still proceeds
        # (_ensure_handoff returns False instead of raising).
        # NOTE: scratch.md is deliberately NOT wiped here. It is the
        # episode's working trace and is only cleared by _close_episode,
        # which archives it first. Wiping it at turn start destroyed the
        # only copy of an interrupted turn's work (issue #13).
        from .handoff import validate_handoff
        if turn_no > 1:
            ok, reason = validate_handoff(session.dir, turn_no - 1)
            if not ok:
                # Force a handoff recovery before any work
                self._ensure_handoff(session, turn_no - 1, system,
                                     messages=[])
        if self._stop_event.is_set():
            # Stop requested before the turn started: honor it.
            self._stop_event.clear()
            self._emit("interrupted", {})
            return "", True
        # Fresh turn, fresh no-match streak: a stall can only come
        # from this turn's own fruitless searches, never from a
        # previous turn's.
        self._no_match_streak = 0
        self._no_match_patterns = []
        # Fresh turn, fresh repeat streak: same rule.
        self._repeat_streak = 0
        self._last_tool_sig = None
        if self.approver is not None:
            self.approver.new_turn()
        if self.tracker is not None and self.tracker.turns:
            self._turn_seq = max(t["turn"]
                                 for t in self.tracker.turns) + 1
        else:
            self._turn_seq += 1
        # The turn's opening message joins history.md first, stamped with
        # the harness-owned turn counter (T6). turn_no was assigned at the
        # top of _run_turn_once so the handoff stamp matches this turn.
        # Auto-turns are labeled `## auto`, never `## user`: the record
        # must distinguish user-initiated from harness-initiated turns.
        render_opening = (render_history_auto if is_auto
                          else render_history_user)
        self._append_source(session, "history.md",
                            render_opening(user_text, turn_no))
        warned = False
        # Initialised before the step loop: the transcript-bloat branch
        # below returns `content` on the very first step, before any
        # provider reply exists. Uninitialised, that was UnboundLocalError
        # for any session whose assembled transcript was already >= 35k
        # tokens at turn start (issue #13).
        content = ""

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
                # Transcript-bloat gate (redesigned; the old hard-coded
                # 35k threshold fired below the soft budget, pre-empting
                # warn/prune, and returned "" even when closing could
                # not shrink the transcript -- a session whose
                # sats+history alone exceeded 35k force-closed every
                # turn and never progressed). New contract:
                #  * threshold is configurable (transcript_bloat_tokens
                #    property): explicit value, else 30% of the known
                #    window, else soft budget when unknown -- never
                #    just assumed from the budget system;
                #  * the gate fires ONLY when closing the episode can
                #    actually get under it, i.e. scratch alone covers
                #    the overage (closing archives scratch and clears
                #    it; sats are untouched by a close, history only
                #    windowed, so sats/history-only bloat must NOT
                #    fire the gate -- it falls through to warn/prune,
                #    which curates or fails loudly via BudgetExceeded);
                #  * the turn CONTINUES after the close instead of
                #    returning "" -- the user's message gets answered.
                # Self-disarming: after a close scratch is empty, so
                # the condition cannot refire on the same content.
                transcript_tokens = bd.get("transcript", 0)
                scratch_tokens = 0
                try:
                    scratch_p = session.dir / "scratch.md"
                    if scratch_p.exists():
                        scratch_tokens = count_tokens(
                            scratch_p.read_text(encoding="utf-8",
                                                errors="replace"))
                except OSError:
                    scratch_tokens = 0
                if (transcript_tokens >= self.transcript_bloat_tokens
                        and scratch_tokens >= transcript_tokens
                        - self.transcript_bloat_tokens):
                    self._emit("budget", {"status": "transcript_bloat",
                                          "tokens": est,
                                          "breakdown": bd,
                                          "threshold":
                                              self.transcript_bloat_tokens,
                                          "scratch_tokens": scratch_tokens})
                    # Force a handoff now to trigger deep clean, then
                    # continue the turn with the shrunken transcript.
                    # No clear() here: a stop requested before this
                    # close must survive to the next step boundary
                    # (_check_stop at the loop top), where it ends
                    # the turn, instead of being eaten by the close.
                    self._auto_dedupe(session)
                    self._ensure_handoff(session, turn_no, system, messages)
                    self._close_episode(session, turn_no, content or "")
                    continue
                if status == "over":
                    self._emit("budget", {"status": "over", "tokens": est,
                                          "breakdown": bd})
                    if not self.prune_enabled:
                        # Naive mode (condition A): truncate oldest at window.
                        self._naive_truncate(session)
                        warned = False
                        continue
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
                    # No clear() here, deliberately: a stop requested
                    # during this turn (e.g. after the last step
                    # check, while the closing handoff/close runs)
                    # must survive to run_turn's between-turns check,
                    # which honors it and stops the chain. Clearing
                    # here ate exactly that stop and left an
                    # auto-continue chain un-stoppable mid-run: the
                    # turn completed, the flag vanished, and the next
                    # auto-turn started. run_turn clears the flag
                    # when it honors it, so nothing leaks into a
                    # later user turn.
                    self._auto_dedupe(session)
                    # Turn-end sats budgets run BEFORE the handoff
                    # check: a repaired `- [ ]` -> `- [active]` line
                    # is a live goal, and the chain decision below
                    # must see it.
                    self._enforce_sats_budgets(session)
                    # Goals structure is machine-checked too: a
                    # malformed goals file triggers fix rounds, and
                    # persistent failure is loud but never blocks
                    # the close (mirrors _ensure_handoff).
                    self._ensure_goals(session, turn_no, system,
                                       messages)
                    # Once every goal is [done], no more handoffs:
                    # the turn that finishes the goal writes nothing
                    # to handoff.md, and the chain already stops
                    # after it (no active goals). A goal-less session
                    # (no [done] line at all) keeps the normal
                    # per-turn handoff.
                    if not goals_done(session.dir):
                        # Handoff-per-turn (issue #5): the turn may
                        # only end with a fresh handoff. Stale/missing
                        # triggers a forced handoff round; persistent
                        # failure is loud but never blocks the close.
                        self._ensure_handoff(session, turn_no, system,
                                             messages)
                    # T7: the episode closes -- archive the trace, clear
                    # scratch, record the pointer. H1: the closing reply
                    # is recorded to history by the harness.
                    self._close_episode(session, turn_no, content or "")
                    return content or "", False
                for tc in tool_calls:
                    self._check_stop()
                    result = self._execute_tool(session, tc)
                    self._append_source(
                        session, "scratch.md",
                        render_tool(tc["id"], result))
                    self._auto_dedupe(session)
        except SearchStall as stall:
            # Loud, and chain-stopping: returning interrupted=True
            # makes run_turn return without chaining, so the stuck
            # model cannot restart the loop on the next auto-turn.
            # The message goes out as a normal assistant event (the
            # transcript's reply path), not a new event kind.
            patterns = ", ".join(f"'{p}'" for p in stall.patterns)
            message = (
                f"Stopped: {len(stall.patterns)} consecutive searches "
                f"returned no matches ({patterns}). The search root "
                f"or pattern is wrong -- fix it or ask the user.")
            self._emit("assistant", message)
            self._enforce_sats_budgets(session)
            return message, True
        except RepeatStall as stall:
            # Same treatment: loud, chain-stopping, normal
            # assistant event. The message names the call so the
            # next turn can vary it instead of repeating it.
            message = (
                f"Stopped: the same {stall.name} call repeated "
                f"{stall.count} times in a row. Vary the call, "
                f"read a file directly, or ask the user.")
            self._emit("assistant", message)
            self._enforce_sats_budgets(session)
            return message, True
        except TurnInterrupted:
            self._stop_event.clear()
            self._emit("interrupted", {})
            return "", True
        raise ProviderError(f"Turn exceeded {MAX_STEPS} steps without "
                            f"finishing.")
