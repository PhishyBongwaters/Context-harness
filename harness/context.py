"""The context file: the model's entire persistent memory.

Token counting prefers tiktoken (cl100k_base) when installed, else falls
back to a chars/4 heuristic. The counter reports which estimator is active
so the model knows how much to trust the meter.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    ESTIMATOR = "tiktoken/cl100k_base"
except Exception:
    _ENC = None
    ESTIMATOR = "heuristic(chars/4)"


def count_tokens(text: str) -> int:
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text) // 4)


@dataclass
class Budget:
    hard: int
    soft: int

    def status(self, tokens: int) -> str:
        if tokens >= self.hard:
            return "over"
        if tokens >= self.soft:
            return "warn"
        return "ok"

    def meter_line(self, tokens: int) -> str:
        pct = 100.0 * tokens / self.hard if self.hard else 0
        return (f"[context budget: {tokens:,}/{self.hard:,} tokens "
                f"({pct:.0f}%) | estimator: {ESTIMATOR}]")


EMPTY_CONTEXT = """# Context

This file is your entire persistent memory. There is no hidden transcript:
when a turn ends, only what is written here survives.

Keep it curated and compact: goals, key facts, decisions, where things stand.
Summarize stale tool output, drop dead ends, keep live threads organized.
Write it for yourself -- the you who reads it next turn.
"""


class ContextFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> str:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(EMPTY_CONTEXT, encoding="utf-8")
            return EMPTY_CONTEXT
        return self.path.read_text(encoding="utf-8", errors="replace")

    def save(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def tokens(self) -> int:
        return count_tokens(self.load())
