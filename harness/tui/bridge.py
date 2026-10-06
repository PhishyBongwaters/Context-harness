"""TUI event bridge: stdlib-only, no Textual import here.

Loop (worker thread) -> on_event(kind, data) -> queue.Queue -> UI thread
polls and renders. Never touches UI objects directly.
"""
from __future__ import annotations

import queue
import threading


def format_event(kind: str, data) -> str | None:
    """Render one loop event as a transcript line. None = not shown."""
    if kind == "assistant":
        return data if isinstance(data, str) else str(data)
    if kind == "tool":
        data = data or {}
        args = data.get("args") or {}
        brief = args.get("command") or args.get("path") or ""
        denied = " [denied]" if data.get("denied") else ""
        return f"$ {data.get('name', '?')} {brief}{denied}".rstrip()
    if kind == "budget":
        data = data or {}
        return (f"[{str(data.get('status', '?')).upper()} budget: "
                f"{data.get('tokens', 0):,} tokens]")
    if kind == "prune":
        data = data or {}
        return (f"[prune-only turn {data.get('attempt', '?')}: "
                f"{data.get('tokens', 0):,} tokens]")
    if kind == "context-diff":
        data = data or {}
        rec = data.get("recovered", 0)
        sign = "+" if rec >= 0 else ""
        lines = [f"[context diff: {sign}{rec:,} tokens]"]
        for r in (data.get("removed") or [])[:5]:
            lines.append(f"  - {r.get('header')} "
                         f"({r.get('tokens', 0):,} tokens)")
        if len(data.get("removed") or []) > 5:
            lines.append(f"  - ... +{len(data['removed']) - 5} more")
        for a in (data.get("added") or [])[:5]:
            lines.append(f"  + {a.get('header')} "
                         f"({a.get('tokens', 0):,} tokens)")
        if len(data.get("added") or []) > 5:
            lines.append(f"  + ... +{len(data['added']) - 5} more")
        return "\n".join(lines)
    if kind == "request":
        data = data or {}
        if "tokens_est" not in data:
            return None
        toks, hard = data["tokens_est"], data.get("hard") or 0
        pct = 100.0 * toks / hard if hard else 0
        bd = data.get("breakdown") or {}
        parts = (f"sys {bd.get('system', 0):,} + "
                 f"chat {bd.get('transcript', 0):,} + "
                 f"tools {bd.get('tools', 0):,}")
        ut = data.get("usage_total") or {}
        sess = (f" | sess in {ut.get('input', 0):,} "
                f"out {ut.get('output', 0):,}") if ut else ""
        return f"[context {toks:,} ({parts}) / {hard:,} ({pct:.0f}%){sess}]"
    if kind == "response":
        data = data or {}
        content = data.get("content")
        if content:
            return content if isinstance(content, str) else str(content)
        calls = data.get("tool_calls") or []
        if calls:
            names = ", ".join(c.get("name", "?") for c in calls)
            return f"[tool calls: {names}]"
        return None
    if kind == "error":
        data = data or {}
        msg = data.get("message") or data.get("type") or data
        return f"[error: {msg}]"
    if kind == "approval-wait":
        data = data or {}
        brief = ((data.get("args") or {}).get("command")
                 or (data.get("args") or {}).get("path") or "")
        return (f"[approval needed] {data.get('tool', '?')} {brief} "
                f"({data.get('timeout', '?')}s, default deny)").rstrip()
    if kind == "approval-result":
        data = data or {}
        return (f"[approval {data.get('decision', '?')} "
                f"({data.get('scope', '?')})]")
    return None  # usage, backup, prune-deterministic: quiet in transcript


class TuiBridge:
    """Thread-safe on_event subscriber feeding the UI thread.

    Suitable as Loop(on_event=bridge). UI polls drain() or reads
    .queue directly; worker threads never touch UI objects.
    Each entry is (kind, data, line) where line is format_event output
    (may be None for quiet events).
    """

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()

    def __call__(self, kind: str, data) -> None:
        try:
            line = format_event(kind, data)
        except Exception:
            line = None
        self.queue.put((kind, data, line))

    def drain(self) -> list:
        """Non-blocking drain of pending entries, in FIFO order."""
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out


def run_turn_in_thread(loop, session, text: str,
                       on_done=None, on_error=None) -> threading.Thread:
    """Run loop.run_turn off the UI thread; never touches UI directly.

    on_done(result) / on_error(exc) fire on the WORKER thread; the UI
    must re-dispatch via call_from_thread.
    """
    def _work():
        try:
            result = loop.run_turn(session, text)
        except Exception as e:  # noqa: BLE001 - report to UI
            if on_error is not None:
                on_error(e)
            return
        if on_done is not None:
            on_done(result)

    t = threading.Thread(target=_work, daemon=True, name="tui-turn")
    t.start()
    return t


def budget_bar_text(data) -> str | None:
    """Compact budget bar from a request event.

    Same ledger as the CLI [context ...] line (see format_event):
    sys/chat/tools from breakdown, sess in/out from usage_total.
    None when the event carries no estimate.
    """
    data = data or {}
    if "tokens_est" not in data:
        return None
    toks, hard = data["tokens_est"], data.get("hard") or 0
    pct = 100.0 * toks / hard if hard else 0
    bd = data.get("breakdown") or {}
    ut = data.get("usage_total") or {}
    sess = (f" | sess in {ut.get('input', 0):,} "
            f"out {ut.get('output', 0):,}") if ut else ""
    return (f"ctx {toks:,} / {hard:,} ({pct:.0f}%) "
            f"sys {bd.get('system', 0):,} "
            f"chat {bd.get('transcript', 0):,} "
            f"tools {bd.get('tools', 0):,}{sess}")


def budget_bar_status(data) -> str:
    """warn/over colour state, honouring the loop's status when present."""
    data = data or {}
    status = data.get("status")
    if status in ("ok", "warn", "over"):
        return status
    if "tokens_est" not in data:
        return "ok"
    toks = data["tokens_est"]
    hard, soft = data.get("hard") or 0, data.get("soft") or 0
    if hard and toks >= hard:
        return "over"
    if soft and toks >= soft:
        return "warn"
    return "ok"


def format_status(phase: str | None, elapsed_s: float) -> str:
    """thinking/pruning Ns elapsed; replaces Spinner in TUI mode."""
    label = "pruning" if phase == "prune" else "thinking"
    return f"{label} {max(0, int(elapsed_s))}s"


# Events that render no transcript line and never break a response/
# assistant pair (usage sits between them in run_turn).
_QUIET_KINDS = ("usage", "backup", "prune-deterministic")


def _same_text(a, b) -> bool:
    return (a or "").strip() == (b or "").strip()


def _tool_calls_note(data) -> str | None:
    calls = (data.get("tool_calls") or []) if isinstance(data, dict) else []
    if not calls:
        return None
    names = ", ".join(c.get("name", "?") for c in calls)
    return f"[tool calls: {names}]"


def dedupe_entries(entries: list) -> list:
    """One-shot filter: drop a main-phase bare response when an assistant
    echo of the same text follows (quiet events skipped over).

    Prune-phase responses have no assistant event and are always kept;
    context-diff/budget/prune/tool lines pass through untouched.
    """
    out: list = []
    i, n = 0, len(entries)
    while i < n:
        kind, data, line = entries[i]
        if (kind == "response" and isinstance(data, dict)
                and data.get("content")
                and data.get("phase", "main") == "main" and line):
            j = i + 1
            while j < n and entries[j][0] in _QUIET_KINDS:
                j += 1
            if (j < n and entries[j][0] == "assistant"
                    and _same_text(entries[j][2], line)):
                note = _tool_calls_note(data)
                if note:
                    out.append((kind, data, note))
                i += 1
                continue
        out.append(entries[i])
        i += 1
    return out


class TranscriptDedupe:
    """Stateful version of dedupe_entries for poll-by-poll draining.

    A bare main-phase response is buffered until the next visible event
    resolves dupe-or-not, so pairs split across polls still collapse.
    feed() returns display lines; flush() emits any held line.
    """

    def __init__(self) -> None:
        self._pending = None  # buffered (kind, data, line)

    def feed(self, entries: list) -> list[str]:
        lines: list[str] = []
        for kind, data, line in entries:
            if self._pending is not None:
                if kind in _QUIET_KINDS:
                    continue  # keep waiting; quiet events show nothing
                pk, pd, pl = self._pending
                self._pending = None
                if kind == "assistant" and _same_text(line, pl):
                    note = (_tool_calls_note(pd)
                            if isinstance(pd, dict) else None)
                    if note:
                        lines.append(note)
                    if line:
                        lines.append(line)
                    continue
                if pl:
                    lines.append(pl)  # no dupe: flush buffered first
                # fall through to handle the current entry below
            if (kind == "response" and isinstance(data, dict)
                    and data.get("content")
                    and data.get("phase", "main") == "main" and line):
                self._pending = (kind, data, line)
                continue
            if line:
                lines.append(line)
        return lines

    def flush(self) -> list[str]:
        if self._pending is not None:
            _, _, pl = self._pending
            self._pending = None
            return [pl] if pl else []
        return []
