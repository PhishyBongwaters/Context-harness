"""TUI widgets + pure rendering helpers (Phase 3).

Pure helpers (stdlib-only, no Textual needed): transcript formatting,
budget bar, status line, dedupe, debug-tail reading. The canonical
definitions live here; bridge.py re-exports them so existing imports
keep working with identical objects (no behaviour change).

Thin Textual widgets (BudgetBar, StatusLine, TranscriptLog,
DebugPanel) exist only when the textual extra is installed.
"""
from __future__ import annotations

from pathlib import Path

# --- transcript formatting (moved from bridge.py, verbatim) ---


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


# --- budget bar + status line (moved from bridge.py, verbatim) ---


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


# --- dedupe (moved from bridge.py, verbatim) ---

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


# --- debug tail (new, Phase 3; stdlib-only, best-effort) ---

DEBUG_TAIL_LINES = 50


def tail_file(path: str | Path | None, n: int = DEBUG_TAIL_LINES,
               ) -> list[str]:
    """Last n lines of a text file. [] when missing/unreadable."""
    if path is None:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-max(1, n):] if lines else []


def debug_panel_lines(path: str | Path | None,
                       n: int = DEBUG_TAIL_LINES) -> list[str]:
    """Tail lines for the debug panel, or a hint when unavailable."""
    lines = tail_file(path, n)
    if lines:
        return lines
    if path is None:
        return ["[debug] no debug log (run with --debug)"]
    return [f"[debug] no log yet at {path} (run with --debug)"]


# --- thin Textual widgets (only with the extra installed) ---

try:
    from textual.widgets import Log, Static

    _HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - extra missing
    _HAS_TEXTUAL = False

if _HAS_TEXTUAL:  # pragma: no cover - needs the extra

    class BudgetBar(Static):
        """One-line budget readout; warn/over tint via CSS classes."""

        def set_request(self, data) -> None:
            text = budget_bar_text(data)
            if text is None:
                return
            self.update(text)
            status = budget_bar_status(data)
            for cls in ("warn", "over"):
                self.remove_class(cls)
            if status in ("warn", "over"):
                self.add_class(status)

    class StatusLine(Static):
        """Elapsed-time line; blank when idle."""

        def set_elapsed(self, phase: str | None,
                        elapsed_s: float) -> None:
            self.update(format_status(phase, elapsed_s))

        def clear(self) -> None:
            self.update("")

    class WrappedLog(Log):
        """Log + manual soft wrap (keeps drag-select working).

        Log is the drag-selectable text widget, but it has no wrap
        support -- long lines scroll horizontally, which reads terribly
        for chat text. So: wrap each write to the widget width
        (cell-aware, wide chars count 2) and rewrap everything on
        terminal resize. Raw lines are kept for the rewrap; Log itself
        keeps selection + ctrl+c copy on the wrapped lines.
        """

        MAX_LINES = 10_000

        def __init__(self, *a, **k) -> None:
            super().__init__(*a, **k)
            self._raw: list[str] = []
            self._wrap_w = 0

        def _wrap_width(self) -> int:
            try:
                w = self.size.width
            except Exception:
                w = 0
            if not w:
                return 78  # not laid out yet; first resize corrects it
            # size is the outer box, so scrollbar show/hide can't change
            # it and rewraps can't oscillate. -2 keeps lines under the
            # scrollable width even when the vertical scrollbar shows.
            return max(20, w - 2)

        @staticmethod
        def _wrap(text: str, width: int) -> list[str]:
            """Greedy wrap by display cells (rich cell_len), not chars."""
            from rich.cells import cell_len

            def _wrap_line(line: str) -> list[str]:
                if not line:
                    return [""]
                if cell_len(line) <= width:
                    return [line]
                out: list[str] = []
                cur = ""
                for word in line.split(" "):
                    # Hard-break words wider than the pane.
                    while cell_len(word) > width:
                        if cur:
                            out.append(cur)
                            cur = ""
                        n = width
                        while cell_len(word[:n]) > width and n > 1:
                            n -= 1
                        out.append(word[:n])
                        word = word[n:]
                    cand = word if not cur else cur + " " + word
                    if cur and cell_len(cand) > width:
                        out.append(cur)
                        cur = word
                    else:
                        cur = cand
                if cur or not out:
                    out.append(cur)
                return out

            return [l for part in (str(text).splitlines() or [str(text)])
                    for l in _wrap_line(part)]

        def write_wrapped(self, text: str) -> None:
            self._raw.append(text)
            if len(self._raw) > self.MAX_LINES:
                del self._raw[:len(self._raw) - self.MAX_LINES]
            for line in self._wrap(text, self._wrap_width()):
                # write(), not write_line, CONCATENATES newline-less
                # strings into one line -- wrapped chunks must each be
                # their own line.
                self.write_line(line)

        def clear_all(self) -> None:
            self._raw.clear()
            self.clear()

        def rewrap(self) -> None:
            w = self._wrap_width()
            if w == self._wrap_w:
                return
            self._wrap_w = w
            self.clear()
            for text in self._raw:
                for line in self._wrap(text, w):
                    self.write_line(line)

        def on_resize(self, event) -> None:
            self.rewrap()

    class TranscriptLog(WrappedLog):
        """Transcript with a deduping write helper.

        Log (not RichLog): RichLog is a scroll *container*, which
        Textual's text-selection machinery never targets -- Log is a
        plain leaf widget with drag-select support built in (plus
        get_selection for ctrl+c copy). Wrapping comes from WrappedLog.
        """

        def __init__(self, *a, **k) -> None:
            super().__init__(*a, **k)
            self._dedupe = TranscriptDedupe()

        def write_entries(self, entries: list) -> None:
            for line in self._dedupe.feed(entries):
                self.write_wrapped(line)

    class DebugPanel(WrappedLog):
        """Live tail of the session debug.jsonl (best-effort)."""

        def refresh_from(self, path: str | Path | None,
                         n: int = DEBUG_TAIL_LINES) -> None:
            try:
                self.clear_all()
            except Exception:
                return
            for line in debug_panel_lines(path, n):
                try:
                    self.write_wrapped(line)
                except Exception:
                    return
