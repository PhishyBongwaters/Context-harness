"""TUI widgets + pure rendering helpers (Phase 3).

Pure helpers (stdlib-only, no Textual needed): transcript formatting,
budget bar, status line, dedupe, debug-tail reading. The canonical
definitions live here; bridge.py re-exports them so existing imports
keep working with identical objects (no behaviour change).

Thin Textual widgets (BudgetBar, StatusLine, TranscriptLog,
DebugPanel) exist only when the textual extra is installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from rich.syntax import Syntax
    from rich.console import Console
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover
    Syntax = None  # kept as names so tests can patch them
    Console = None
    _RICH_AVAILABLE = False

import re as _re

_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")
_FENCE_RE = _re.compile(r"```(\w*)\n(.*?)```", _re.S)


def strip_ansi(text: str | None) -> str:
    """Remove SGR escape sequences (keeps exported transcripts readable)."""
    return _ANSI_RE.sub("", text or "")


def highlight_fenced_code(text: str) -> str:
    """Render ```lang fenced blocks through Rich Syntax, like a code editor.

    Falls back to the raw text when Rich is missing or a lexer blows up.
    """
    if not _RICH_AVAILABLE or "```" not in (text or ""):
        return text

    def _one(m) -> str:
        lang = (m.group(1) or "").strip()
        code = m.group(2)
        try:
            console = Console(record=True, color_system="standard",
                              force_terminal=True)
            console.print(Syntax(code, lang or "text", theme="monokai",
                                 line_numbers=False))
            return console.export_text(styles=True).rstrip("\n")
        except Exception:
            return m.group(0)

    try:
        return _FENCE_RE.sub(_one, text)
    except Exception:
        return text

# --- transcript formatting (moved from bridge.py, verbatim) ---


def _ansi(text: str, *codes: int) -> str:
    if not _RICH_AVAILABLE:
        return text
    # Rich Console can render markup to ANSI; use simple prefix for speed
    # Fallback to plain text if Rich not available
    try:
        from rich.console import Console
        # Build a simple ANSI sequence
        seq = "".join(f"\x1b[{c}m" for c in codes)
        return f"{seq}{text}\x1b[0m"
    except Exception:
        return text


# Chat-style message backgrounds (tasteful dark tints).
_USER_BG = "#1d2b3a"       # blue-grey for user messages
_ASSISTANT_BG = "#2b2b2b"  # warm dark grey for agent replies
_TOOL_BG = "#232323"       # slightly darker for tool calls


def _bg_code(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"\x1b[48;2;{r};{g};{b}m"


def with_bg(line: str, hex_color: str) -> str:
    """Tag a line with its background color for post-wrap application.

    On Windows, returns the line unchanged: conhost's ANSI handling
    makes background panels fundamentally unreliable (cursor drift,
    ghost fragments, jagged padding across 7 attempted fixes). The
    me/assistant labels already distinguish messages; correctness
    beats decoration.
    """
    import sys
    if sys.platform == "win32":
        return line
    if not line or not _RICH_AVAILABLE:
        return line
    return f"\x00{hex_color}\x00" + line


def _apply_bg(line: str, hex_color: str) -> str:
    """Apply 16-color ANSI background to a single (already-wrapped) line."""
    if hex_color == _USER_BG:
        bg = "\x1b[44m"      # blue for user
    elif hex_color == _ASSISTANT_BG:
        bg = "\x1b[100m"     # bright black for assistant
    else:
        bg = "\x1b[40m"      # black for tools/other
    out = []
    for part in line.split("\n"):
        out.append(bg + part.replace("\x1b[0m", "\x1b[0m" + bg)
                   + "\x1b[0m")
    return "\n".join(out)


_BG_RE = _re.compile(r"\x1b\[(?:40|44|100)m")


def _panel(line: str, width: int) -> str:
    """Pad a with_bg line to full width (chat panel effect).

    On Windows, returns the line unchanged: conhost's handling of
    padded background spans creates jagged edges and ghost artifacts.
    The text still carries its background color, just not full-width.
    """
    import sys
    if sys.platform == "win32":
        return line
    m = _BG_RE.search(line)
    if not m or width <= 0:
        return line
    width = width - 1
    bg = m.group(0)
    vis = len(strip_ansi(line))
    if vis >= width:
        return line
    if not line.startswith(bg):
        line = bg + line
    pad = bg + " " * (width - vis)
    if line.endswith("\x1b[0m"):
        return line[:-4] + pad + "\x1b[0m"
    return line + pad + "\x1b[0m"

def format_event(kind: str, data) -> str | None:
    """Render one loop event as a transcript line. None = not shown."""
    if kind == "assistant":
        txt = data if isinstance(data, str) else str(data)
        if txt:
            # green bold assistant label; fenced code blocks highlighted;
            # whole reply rides a subtle dark background, chat-style
            return with_bg(
                f"{_ansi('assistant',1,32)}\n{highlight_fenced_code(txt)}",
                _ASSISTANT_BG)
        return None
    if kind == "tool":
        data = data or {}
        name = data.get("name", "?")
        args = data.get("args") or {}
        brief = args.get("command") or args.get("path") or ""
        denied = " [denied]" if data.get("denied") else ""
        header = f"{_ansi('🔧 '+name+' '+brief+denied,2)}".rstrip()
        # Inline syntax-highlighted view for write/edit of source files
        if _RICH_AVAILABLE and name in ("write", "edit"):
            path = args.get("path")
            if path:
                try:
                    p = Path(path)
                    if p.is_file():
                        # Limit to reasonable size to keep TUI responsive
                        try:
                            text = p.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            text = ""
                        if text:
                            # Cap to ~20KB / 500 lines
                            lines = text.splitlines()
                            if len("\n".join(lines)) > 20000:
                                lines = lines[:500]
                                text = "\n".join(lines) + "\n…"
                            try:
                                syn = Syntax(text, lexer=None, theme="monokai", line_numbers=False)
                                console = Console(record=True, color_system="standard", force_terminal=True)
                                console.print(syn)
                                code_blob = console.export_text(styles=True)
                            except Exception:
                                code_blob = text
                            if code_blob:
                                return with_bg(
                                    f"{header}\n{_ansi('file',1)} {p}\n{code_blob}",
                                    _TOOL_BG)
                except Exception:
                    pass
        return with_bg(header, _TOOL_BG)
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
            text = content if isinstance(content, str) else str(content)
            return highlight_fenced_code(text)
        calls = data.get("tool_calls") or []
        if calls:
            names = ", ".join(c.get("name", "?") for c in calls)
            return f"[tool calls: {names}]"
        return None
    if kind == "error":
        data = data or {}
        msg = data.get("message") or data.get("type") or data
        return f"[error: {msg}]"
    if kind == "interrupted":
        return "[turn interrupted]"
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


def gauge_blocks(tokens: int, hard: int, width: int = 20) -> str:
    """Filled/empty block gauge string for tokens of hard."""
    if hard <= 0:
        return "░" * width
    filled = max(0, min(width, round(width * tokens / hard)))
    return "█" * filled + "░" * (width - filled)


def gauge_line(data, status: str = "", width: int = 20) -> str:
    """Gauge + pct + totals (+ status text) from a request event.

    Before the first request (no tokens_est) just the status text, so
    the bottom line behaves like the old status line until data lands.
    """
    data = data or {}
    if "tokens_est" not in data:
        return status or ""
    toks, hard = data["tokens_est"], data.get("hard") or 0
    pct = (100.0 * toks / hard) if hard else 0.0
    blocks = gauge_blocks(toks, hard, width)
    line = f"[{blocks}] {pct:3.0f}% {toks:,}/{hard:,}"
    if status:
        line = f"{line} · {status}"
    return line


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


def _dupe_text(kind: str, data) -> str:
    """Comparable text for response/assistant dupe detection.

    Compares the underlying content, NOT the formatted line: the
    assistant line always carries a label prefix (and possibly ANSI
    highlighting), so formatted lines can never be equal even for the
    same reply. Comparing data is immune to formatting changes.
    """
    if kind == "assistant":
        return data if isinstance(data, str) else str(data or "")
    if kind == "response" and isinstance(data, dict):
        content = data.get("content")
        return content if isinstance(content, str) else str(content or "")
    return ""


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
                    and _same_text(_dupe_text("assistant", entries[j][1]),
                                   _dupe_text(kind, data))):
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
                if kind == "assistant" and _same_text(
                        _dupe_text("assistant", data),
                        _dupe_text("response", pd)):
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


# --- input history (stdlib-only; TaskInput widget below uses it) ---


class InputHistory:
    """Up/down recall for submitted inputs, with draft preservation.

    Pure state machine, no Textual needed: older()/newer() take the
    current box text and return what the box should show. Editing a
    recalled entry restarts navigation from that edit as the draft.
    """

    def __init__(self, limit: int = 200) -> None:
        self._items: list[str] = []
        self._limit = max(1, limit)
        self._pos: int | None = None  # None = not navigating
        self._draft = ""

    def __len__(self) -> int:
        return len(self._items)

    def add(self, text: str) -> None:
        """Record a submitted input. Consecutive dupes collapse."""
        if text and (not self._items or self._items[-1] != text):
            self._items.append(text)
            del self._items[:-self._limit]
        self._pos = None
        self._draft = ""

    def _restart_if_edited(self, current: str) -> None:
        if (self._pos is not None
                and current != self._items[self._pos]):
            # Edited while navigating: the edit becomes the draft.
            self._draft = current
            self._pos = None

    def older(self, current: str) -> str:
        """Step to an older entry (up arrow)."""
        if not self._items:
            return current
        self._restart_if_edited(current)
        if self._pos is None:
            self._draft = current
            self._pos = len(self._items) - 1
        elif self._pos > 0:
            self._pos -= 1
        return self._items[self._pos]

    def newer(self, current: str) -> str:
        """Step toward newer entries (down arrow); past the newest the
        preserved draft returns."""
        if self._pos is None:
            return current
        self._restart_if_edited(current)
        if self._pos is None:
            return current
        if self._pos < len(self._items) - 1:
            self._pos += 1
            return self._items[self._pos]
        self._pos = None
        return self._draft


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
    from textual.binding import Binding
    from textual.widgets import TextArea

    class TaskInput(TextArea):
        """Multi-line task box: enter sends, ctrl+n inserts a newline,
        up/down recalls history on single-line input.

        Enter-to-send matches the old single-line Input muscle memory.
        Ctrl+n is the reliable cross-terminal newline (modified enters
        are terminal-dependent); newlines also come from pasting. History navigation
        applies to single-line input; with multiple lines up/down move
        the cursor normally. Submitted inputs feed the shared
        InputHistory (dupes collapse, draft preserved).
        """

        BINDINGS = [
            Binding("ctrl+enter", "insert_newline", "", show=False),
            Binding("up", "history_up", "", show=False),
            Binding("down", "history_down", "", show=False),
        ]

        def __init__(self, *a, on_submit=None, history=None, **k) -> None:
            super().__init__(*a, **k)
            self.border_title = ("enter send · ctrl+n newline · "
                                 "up/down history")
            self._on_submit = on_submit
            self._input_history = history if history is not None else InputHistory()

        @property
        def input_history(self) -> InputHistory:
            return self._input_history

        def _recall(self, text: str) -> None:
            self.text = text
            try:
                lines = text.split("\n")
                self.move_cursor((len(lines) - 1, len(lines[-1])))
            except Exception:
                pass

        def action_submit_task(self) -> None:
            text = self.text.strip()
            if not text:
                return
            self._input_history.add(text)
            self.text = ""
            if self._on_submit is not None:
                self._on_submit(text)

        async def _on_key(self, event) -> None:
            # TextArea swallows Enter (inserts "\n") in its own _on_key
            # before widget bindings are consulted, so intercept here.
            # Enter always sends. Modified enters insert a newline on
            # terminals that deliver them distinctly; ctrl+n is the
            # reliable cross-terminal newline (see app BINDINGS).
            # (async on textual 3.x and 8.x alike.)
            key = event.key
            if key == "enter":
                event.stop()
                event.prevent_default()
                self.action_submit_task()
                return
            if key in ("shift+enter", "ctrl+enter", "alt+enter"):
                event.stop()
                event.prevent_default()
                self.action_insert_newline()
                return
            # TextArea binds ctrl+d (delete_right) and ctrl+y (redo);
            # the app uses them for panel toggles, so intercept here.
            if key == "ctrl+d":
                event.stop()
                event.prevent_default()
                try:
                    self.app.action_toggle_debug()
                except Exception:
                    pass
                return
            if key == "ctrl+y":
                event.stop()
                event.prevent_default()
                try:
                    self.app.action_toggle_system()
                except Exception:
                    pass
                return
            await super()._on_key(event)

        def action_insert_newline(self) -> None:
            try:
                self.insert("\n")
            except Exception:
                pass

        def action_history_up(self) -> None:
            if "\n" in self.text:
                super().action_cursor_up()
                return
            self._recall(self._input_history.older(self.text))

        def action_history_down(self) -> None:
            if "\n" in self.text:
                super().action_cursor_down()
                return
            self._recall(self._input_history.newer(self.text))

    class BudgetGauge(Static):
        """Bottom-line context gauge: block bar + pct + live status.

        The status text (thinking/pruning Ns) rides the same line so
        the bottom of the screen reads as one instrument: gauge left,
        activity right. Warn/over tint via CSS classes.
        """

        def __init__(self, *a, **k) -> None:
            super().__init__("", *a, **k)
            self._request = None
            self._status = ""

        def set_request(self, data) -> None:
            self._request = data
            self._refresh_line()

        def set_status(self, text: str) -> None:
            self._status = text or ""
            self._refresh_line()

        def _refresh_line(self) -> None:
            self.update(gauge_line(self._request, self._status))

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
            # Log measures raw string length, ANSI bytes included, so any
            # styled line looks overflow-wide. The terminal interprets the
            # passthrough ANSI fine; soft-wrap already handles real width,
            # so a horizontal scrollbar is never legitimate here.
            self.show_horizontal_scrollbar = False

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
            w = self._wrap_width()
            # Extract bg color marker (if any), wrap plain text, then
            # apply background to each wrapped chunk. This keeps multiline
            # backgrounds intact without ghosting.
            bg_color = None
            if text.startswith("\x00#") and "\x00" in text[3:]:
                end = text.index("\x00", 3)
                bg_color = text[1:end]
                text = text[end + 1:]
            for line in self._wrap(text, w):
                # write(), not write_line, CONCATENATES newline-less
                # strings into one line -- wrapped chunks must each be
                # their own line.
                if bg_color:
                    line = _apply_bg(line, bg_color)
                self.write_line(_panel(line, w))

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
                bg_color = None
                if text.startswith("\x00#") and "\x00" in text[3:]:
                    end = text.index("\x00", 3)
                    bg_color = text[1:end]
                    text = text[end + 1:]
                for line in self._wrap(text, w):
                    if bg_color:
                        line = _apply_bg(line, bg_color)
                    self.write_line(_panel(line, w))

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

    class SystemPanel(WrappedLog):
        """System messages (errors, prune, approvals...) kept out of
        the chat transcript. Small, top-docked, toggleable."""

        MAX_LINES = 200

        def syslog(self, line: str | None) -> None:
            if not line:
                return
            try:
                self.write_wrapped(line)
            except Exception:
                pass
