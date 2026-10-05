"""Debug logging: JSONL tee of every harness event.

One line per event: {"ts", "session", "kind", "data"}.
Lives next to the transcript by default (<session-dir>/debug.jsonl),
so it never pollutes the repo. Pass an explicit path to put it
elsewhere (e.g. ./.debug/harness.jsonl).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _safe(obj):
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return repr(obj)


class DebugLog:
    def __init__(self, path: str | Path, session_id: str = ""):
        self.path = Path(path)
        self.session_id = session_id

    def write(self, kind: str, data) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"ts": _utcnow(), "session": self.session_id,
                               "kind": kind, "data": _safe(data)},
                              ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # debugging must never break the loop

    def handler(self, wrapped=None):
        """Return an on_event handler that tees to this file."""
        def on_event(kind: str, data) -> None:
            self.write(kind, data)
            if wrapped is not None:
                wrapped(kind, data)
        return on_event
