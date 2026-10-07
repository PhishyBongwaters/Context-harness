"""LCARS chrome for the TUI: palette, bar-segment models, header/footer.

Tasteful by design: only the chrome (header/footer) gets the LCARS
treatment, in the classic TNG palette, with one gently pulsing accent
segment. Everything content-bearing keeps its existing styling.

Pure helpers (palette, _dim, header_segments, footer_segments,
_binding_pills) are stdlib-only and unit-tested. The Textual widgets
need the textual extra and skip cleanly without it.
"""
from __future__ import annotations

# Classic TNG LCARS palette (hex).
LCARS = {
    "orange":     "#FF9900",
    "peach":      "#FFCC99",
    "mauve":      "#CC99CC",
    "periwinkle": "#9999FF",
    "ice":        "#CCDDFF",
    "sky":        "#9999CC",
    "red":        "#CC3333",
    "black":      "#000000",
}

_FG = "#000000"  # LCARS pills carry dark text


def _dim(hex_color: str, factor: float = 0.82) -> str:
    """Slightly darken a #RRGGBB color (for the gentle pulse phase)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (f"#{int(r * factor):02X}{int(g * factor):02X}"
            f"{int(b * factor):02X}")


def header_segments(title: str, width: int, phase: int = 0,
                    ) -> list[tuple[str, str, str]]:
    """(text, bg, fg) segments for the LCARS header bar.

    Brand pill left, title pill next, decorative pills right; the last
    accent pill gently pulses between two shades on odd phases.
    Total text width never exceeds `width`.
    """
    segs: list[tuple[str, str, str]] = []
    segs.append((" LCARS ", LCARS["orange"], _FG))
    segs.append((" ", LCARS["black"], _FG))

    max_title = max(8, width - 44)
    t = title if len(title) <= max_title else title[:max_title - 1] + "\u2026"
    segs.append((f" {t} ", LCARS["mauve"], _FG))

    accent = _dim(LCARS["orange"]) if phase % 2 else LCARS["orange"]
    right = [(" 01 ", LCARS["periwinkle"], _FG),
             (" ", LCARS["black"], _FG),
             (" 02 ", LCARS["peach"], _FG),
             (" ", LCARS["black"], _FG),
             (" \u25c6 ", accent, _FG)]
    used = sum(len(text) for text, _, _ in segs)
    right_w = sum(len(text) for text, _, _ in right)
    filler_w = max(1, width - used - right_w)
    segs.append((" " * filler_w, LCARS["black"], _FG))
    segs.extend(right)
    return segs


def _binding_pills(bindings) -> list[tuple[str, str, str]]:
    """(key, description, action) triples from App.BINDINGS, hidden ones
    skipped.

    Accepts Binding objects or plain (key, action, description[, show])
    tuples so the pure helper stays testable without Textual.
    """
    pills: list[tuple[str, str, str]] = []
    for b in bindings or []:
        if isinstance(b, tuple):
            parts = list(b) + [None, None, None]
            key, action, desc, show = parts[0], parts[1], parts[2], parts[3]
            show = True if show is None else bool(show)
        else:
            key = getattr(b, "key", "")
            action = getattr(b, "action", "")
            desc = getattr(b, "description", "")
            show = getattr(b, "show", True)
        if show is False or not desc:
            continue
        pills.append((str(key), str(desc), str(action)))
    return pills


_PILL_COLORS = ["orange", "mauve", "periwinkle", "peach", "sky", "ice"]


def footer_segments(pills: list[tuple[str, str]], width: int,
                    ) -> list[tuple[str, str, str]]:
    """(text, bg, fg) segments: each key binding as an LCARS pill."""
    segs: list[tuple[str, str, str]] = []
    for i, (key, label) in enumerate(pills):
        color = LCARS[_PILL_COLORS[i % len(_PILL_COLORS)]]
        segs.append((f" {key} {label} ", color, _FG))
        segs.append((" ", LCARS["black"], _FG))
    return segs


try:
    from textual.widgets import Static
    from textual.containers import Horizontal
    from rich.text import Text

    _HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - extra missing
    _HAS_TEXTUAL = False

if _HAS_TEXTUAL:  # pragma: no cover - needs the extra

    def _to_text(segs: list[tuple[str, str, str]]) -> "Text":
        t = Text(no_wrap=True)
        for text, bg, fg in segs:
            t.append(text, style=f"{fg} on {bg}")
        return t

    class LcarsHeader(Static):
        """LCARS top bar: brand pill, title pill, decorative segments.

        One accent segment gently pulses (~1.6s); everything else static.
        """

        def __init__(self, title: str = "", **k) -> None:
            super().__init__(**k)
            self._title = title or ""
            self._phase = 0

        def on_mount(self) -> None:
            self.set_interval(1.6, self._pulse)

        def _pulse(self) -> None:
            self._phase ^= 1
            self.refresh()

        def set_title(self, title: str) -> None:
            self._title = title or ""
            self.refresh()

        def render(self) -> "Text":
            width = self.size.width or 80
            return _to_text(header_segments(self._title, width,
                                            self._phase))

    class LcarsPill(Static):
        """One clickable LCARS pill: key hint + label, runs an app action.

        Mouse-only affordance; keyboard users have the key binding itself.
        """

        def __init__(self, key: str, label: str, action: str, bg: str,
                     **k) -> None:
            super().__init__(**k)
            self._key = key
            self._label = label
            self._action = action
            self._bg = bg

        def render(self) -> "Text":
            t = Text(no_wrap=True)
            t.append(f" {self._key} {self._label} ",
                     style=f"{_FG} on {self._bg}")
            return t

        def on_click(self, event) -> None:
            event.stop()
            fn = getattr(self.app, f"action_{self._action}", None)
            if callable(fn):
                fn()

    class LcarsFooter(Horizontal):
        """LCARS bottom bar: key bindings as clickable colored pills."""

        def __init__(self, pills: list[tuple[str, str, str]] | None = None,
                     **k) -> None:
            super().__init__(**k)
            self._pills = pills or []

        def compose(self):
            for i, (key, label, action) in enumerate(self._pills):
                color = LCARS[_PILL_COLORS[i % len(_PILL_COLORS)]]
                yield LcarsPill(key, label, action, color)


def lcars_theme():
    """The LCARS theme: toggleable in the command palette.

    Registers as 'lcars' via App.register_theme. Pure helper (no
    Textual import at module level) so it stays importable without
    the extra; returns None when textual is missing.
    """
    try:
        from textual.theme import Theme
    except ImportError:
        return None
    return Theme(
        name="lcars",
        primary="#FF9900",      # LCARS orange
        secondary="#CC99CC",    # mauve
        accent="#9999FF",       # periwinkle
        warning="#FFCC99",      # peach
        error="#CC3333",        # LCARS red
        success="#4EBF71",
        foreground="#e0e0e0",
        background="#121212",
        surface="#1e1e1e",
        panel="#1e1e1e",
        dark=True,
    )
