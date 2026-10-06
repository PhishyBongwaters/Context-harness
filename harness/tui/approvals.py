"""TUI approver stub (Phase 1).

Pluggable hook for the Phase 2 approval modal:

    decide: Callable[[dict], str | None] | None
        Called with {"tool", "args", "reason", "timeout"}; return
        "turn" | "session" | "deny", or None for no answer.
        Phase 2 will wire this to an asyncio.Future resolved by the
        modal (a/s/d keys); timeout/EOF still maps to deny.

Phase 1: decide is None, so every ask denies immediately (same
outcome as a CLI timeout) without touching stdin. Never uses
StdinPump; safe to call from a Loop worker thread.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..approvals import APPROVAL_TIMEOUT, Approver


class TUIApprover(Approver):
    def __init__(self, session_dir: str | Path, on_event=None,
                 approval_timeout: int = APPROVAL_TIMEOUT,
                 auto_approve: bool = False,
                 decide: Callable[[dict], str | None] | None = None):
        super().__init__(session_dir, on_event=on_event,
                         approval_timeout=approval_timeout,
                         auto_approve=auto_approve)
        self.decide = decide
        self.pending: dict | None = None

    def _read_answer(self) -> str | None:
        # No stdin here: Phase 1 has no modal, so no answer -> deny.
        # Phase 2 sets self.decide to resolve via the UI future.
        return None

    def _prompt(self, name: str, args: dict, reason: str) -> str:
        if self.decide is not None:
            try:
                ans = self.decide({"tool": name, "args": args or {},
                                   "reason": reason,
                                   "timeout": self.approval_timeout})
            except Exception:
                ans = None
            if ans in ("turn", "session", "deny"):
                return ans
            if isinstance(ans, str):
                low = ans.strip().lower()
                if low in ("a", "approve", "turn", "y", "yes"):
                    return "turn"
                if low in ("s", "session", "always"):
                    return "session"
            return "deny"
        self.pending = {"tool": name, "args": args or {}, "reason": reason}
        try:
            return "deny"  # deny-on-timeout default until modal lands
        finally:
            self.pending = None
