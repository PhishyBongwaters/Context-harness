"""Optional Textual TUI (Phase 1 scaffold).

Requires: textual (see requirements-tui.txt). Core CLI never imports
it: check has_tui() before launching, exit 2 with an install hint when
missing.
"""
from __future__ import annotations


def has_tui() -> bool:
    """True when the textual extra is importable."""
    try:
        import textual  # noqa: F401
        return True
    except ImportError:
        return False
