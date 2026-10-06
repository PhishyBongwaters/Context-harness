"""TUI approver (Phase 2): modal answer instead of StdinPump.

The Loop worker thread blocks in Approver.resolve -> _prompt -> decide,
where decide is set by the Textual app to show the approval modal and
wait on a threading.Event. Timeout/exception/None all map to deny, the
same default-deny as the CLI. approval-wait/approval-result events flow
through the inherited Approver.resolve path (never re-emitted here).

Never touches StdinPump or stdin: _read_answer is hard None, and the
modal path only waits on the UI event.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from ..approvals import APPROVAL_TIMEOUT, Approver


def normalize_answer(ans) -> str:
    """Map a modal/key answer to turn/session/deny. None/other -> deny."""
    if ans in ("turn", "session", "deny"):
        return ans
    if ans is None:
        return "deny"
    low = str(ans).strip().lower()
    if low in ("a", "approve", "turn", "y", "yes"):
        return "turn"
    if low in ("s", "session", "always"):
        return "session"
    return "deny"


def approval_brief(args: dict) -> str:
    """One-line tool summary for the modal (command or path, capped)."""
    brief = str((args or {}).get("command")
                or (args or {}).get("path") or "").strip()
    if len(brief) > 200:
        brief = brief[:200] + "..."
    return brief


def call_with_timeout(fn: Callable, timeout: float):
    """Run fn() on a daemon thread; its value, or None on timeout/error."""
    box: list = []

    def _run():
        try:
            box.append(fn())
        except Exception:  # noqa: BLE001 - errors mean deny
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None


class TUIApprover(Approver):
    def __init__(self, session_dir: str | Path, on_event=None,
                 approval_timeout: int = APPROVAL_TIMEOUT,
                 auto_approve: bool = False,
                 decide: Callable[[dict], str | None] | None = None):
        super().__init__(session_dir, on_event=on_event,
                         approval_timeout=approval_timeout,
                         auto_approve=auto_approve)
        # decide(info) -> "turn" | "session" | "deny" | None, where info
        # is {"tool", "args", "reason", "timeout"}. The app sets this to
        # the modal hook; None denies immediately without prompting.
        self.decide = decide
        self.pending: dict | None = None

    def _read_answer(self) -> str | None:
        # No stdin here, ever. The modal answers via decide's UI event.
        return None

    def _prompt(self, name: str, args: dict, reason: str) -> str:
        info = {"tool": name, "args": args or {}, "reason": reason,
                "timeout": self.approval_timeout}
        self.pending = dict(info)
        try:
            if self.decide is None:
                return "deny"  # no modal wired: deny-on-timeout default
            # The modal lives on the UI thread; cap the worker's wait at
            # approval_timeout so a dismissed modal still denies on time.
            ans = call_with_timeout(lambda: self.decide(info),
                                    self.approval_timeout)
            return normalize_answer(ans)
        finally:
            self.pending = None
