"""Server-side usage ledger + request measuring.

The context window is input AND output: what we send (system prompt,
transcript, tool schemas) plus what the model generates. This module
records both per turn in <session-dir>/usage.json so the CLI, a future
TUI/GUI, and --debug all read the same numbers:

  {"totals": {"input": N, "output": M, "estimated": E},
   "turns": [{"ts", "turn", "phase", "step",
              "breakdown": {"system", "transcript", "tools", "total"},
              "server": {"input", "output"}}]}
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class UsageTracker:
    def __init__(self, session_dir: str | Path):
        self.dir = Path(session_dir)
        self.file = self.dir / "usage.json"
        self.totals = {"input": 0, "output": 0, "estimated": 0}
        self.turns: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.file.is_file():
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.totals = data.get("totals", self.totals)
                    turns = data.get("turns", [])
                    if isinstance(turns, list):
                        self.turns = turns
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(
                {"totals": self.totals, "turns": self.turns},
                ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            pass

    def record(self, *, turn: int, phase: str, step: int,
               breakdown: dict, server: dict | None) -> dict:
        """Log one model call. Returns the updated totals."""
        server = server or {}
        entry = {"ts": _utcnow(), "turn": turn, "phase": phase, "step": step,
                 "breakdown": breakdown,
                 "server": {"input": server.get("input", 0),
                            "output": server.get("output", 0)}}
        self.turns.append(entry)
        self.totals["input"] += entry["server"]["input"]
        self.totals["output"] += entry["server"]["output"]
        self.totals["estimated"] += breakdown.get("total", 0)
        self._save()
        return dict(self.totals)
