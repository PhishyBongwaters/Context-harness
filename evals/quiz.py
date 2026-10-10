"""Retention quiz: does the pruned context still contain the signal?

Protocol (from docs/evaluation-plan.md):
1. Run a task until a prune event fires.
2. Freeze the pre-prune context.
3. Quiz a fresh model instance with ONLY the pruned file.
4. retention_score = fraction of needle questions answered correctly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class QuizResult:
    task_id: str
    condition: str  # "A" or "B"
    prune_event: int
    tokens_before: int
    tokens_after: int
    questions: list[dict] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "condition": self.condition,
            "prune_event": self.prune_event,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "questions": self.questions,
            "score": self.score,
        }


def check_answer(predicted: str, expected: str) -> bool:
    """Lenient match: expected substring in predicted (case-insensitive)."""
    return expected.lower() in predicted.lower()


def run_quiz(task: dict, pruned_text: str, answer_fn) -> QuizResult:
    """Quiz a model (via answer_fn) on the needle using only pruned_text.

    answer_fn(question, context) -> str: calls the model with the pruned
    context and returns its answer.
    """
    result = QuizResult(
        task_id=task["id"],
        condition="",
        prune_event=0,
        tokens_before=0,
        tokens_after=len(pruned_text.split()),
    )
    question = task["needle_question"]
    expected = task["needle_answer"]
    try:
        predicted = answer_fn(question, pruned_text)
    except Exception as e:
        predicted = f"ERROR: {e}"
    correct = check_answer(predicted, expected)
    result.questions.append({
        "question": question,
        "expected": expected,
        "predicted": predicted,
        "correct": correct,
    })
    result.score = 1.0 if correct else 0.0
    return result
