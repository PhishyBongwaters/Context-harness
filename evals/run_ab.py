"""A/B retention evaluation runner — real multi-turn sessions.

Usage:
    python -m evals.run_ab --provider <name> --model <id> \
        --project-dir D:/projects/context-harness

Runs each task under:
  A (--no-prune): naive, append-only, truncate oldest at window
  B (default):    full harness (budget + janitor + deterministic prune)

How it works (like Rob's manual test):
  1. Fresh session, empty history.
  2. Turn 1: "Explain the agent loop." Model reads harness/loop.py
     and related files via tools, building real context.
  3. Turns 2-4: follow-up questions requiring more file reads.
     Context grows to 30k+ tokens from real tool output.
  4. Pruning triggers (condition B) or naive truncation (condition A).
  5. Final turn: ask about a specific detail from turn 1.
     The model must recall it from the (pruned) context.

The model participates in building the context — no pre-loaded fake
history. This tests what actually matters: does the harness preserve
what the model needs across pruning?

Requires a configured provider (API key in env, or local server).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running as `python -m evals.run_ab` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.tasks.harness_tasks import TASKS
from evals.quiz import check_answer
from harness.config import load_config
from harness.context import Budget, count_tokens
from harness.loop import Loop, Session


# The task: 5 real questions about the harness codebase.
# Each task has the model investigate via tools, then gets quizzed.
# TASKS is imported from evals.tasks.harness_tasks above.


def run_condition(task: dict, condition: str, cfg, tmpdir: Path,
                  project_dir: str) -> dict:
    """Run one task under one condition. Returns metrics dict."""
    from harness.providers import make_provider

    prune_enabled = (condition == "B")
    provider = make_provider(cfg, provider=cfg.provider, model=cfg.model,
                             base_url=cfg.base_url, api_key=cfg.api_key)
    # Eval budget: small enough that real work triggers pruning.
    budget = Budget(hard=30_000, soft=20_000,
                    window=getattr(cfg, "context_window", None))
    prune_snapshots = []

    def on_event(kind, data):
        if kind in ("prune-deterministic", "prune", "naive-truncate"):
            pass

    loop = Loop(provider, budget, on_event=on_event,
                prune_provider=provider,
                prune_enabled=prune_enabled,
                prune_target=cfg.prune_target,
                prune_keep_tools=cfg.prune_keep_tools,
                prune_section_cap=cfg.prune_section_cap)

    # Fresh session. Model builds context itself via tool calls.
    # workdir is the real project so read/list_dir tools work.
    sess_dir = tmpdir / f"eval-{task['id']}-{condition}"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess = Session(id=f"{task['id']}-{condition}", dir=sess_dir,
                   workdir=project_dir)

    # Wrap prune to snapshot.
    orig_prune = loop.prune_turn
    def wrapped_prune(s):
        pre = s.context.load()
        tb = count_tokens(pre)
        ok = orig_prune(s)
        ta = count_tokens(s.context.load())
        prune_snapshots.append((tb, pre, ta))
        return ok
    loop.prune_turn = wrapped_prune
    orig_naive = loop._naive_truncate
    def wrapped_naive(s):
        pre = s.context.load()
        tb = count_tokens(pre)
        orig_naive(s)
        ta = count_tokens(s.context.load())
        prune_snapshots.append((tb, pre, ta))
    loop._naive_truncate = wrapped_naive

    tokens_before = 0
    start = time.time()
    try:
        # Turn 1: the model investigates and encounters the needle.
        a1 = loop.run_turn(sess, task["turn1"])
        tokens_before = count_tokens(sess.context.load())

        # Follow-up turns: more investigation, growing context.
        for followup in task["followups"]:
            loop.run_turn(sess, followup)

        # Final turn: retention quiz on turn 1 detail.
        answer = loop.run_turn(sess, task["final_question"])
        success = check_answer(answer or "", task["needle_answer"])
    except Exception as e:
        answer = f"ERROR: {e}"
        success = False
    wall = time.time() - start

    final_ctx = sess.context.load()
    quiz_scores = []
    for tb, pre_text, ta in prune_snapshots:
        survived = task["needle_answer"].lower() in final_ctx.lower()
        quiz_scores.append({
            "tokens_before": tb,
            "tokens_after": ta,
            "needle_survived": survived,
        })

    return {
        "task_id": task["id"],
        "condition": condition,
        "success": success,
        "answer": (answer or "")[:200],
        "expected": task["needle_answer"],
        "tokens_before_run": tokens_before,
        "tokens_after_run": count_tokens(final_ctx),
        "prune_events": len(prune_snapshots),
        "quiz": quiz_scores,
        "wall_time_s": round(wall, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--project-dir", required=True,
                    help="Real project directory for the model to investigate "
                         "(e.g. D:/projects/context-harness)")
    ap.add_argument("--out", default=None, help="JSONL output path")
    args = ap.parse_args()

    cfg = load_config()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model

    project_dir = str(Path(args.project_dir).resolve())
    if not Path(project_dir).is_dir():
        print(f"Project dir not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    import tempfile
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for task in TASKS:
            for cond in ("A", "B"):
                print(f"Running {task['id']} condition {cond}...",
                      file=sys.stderr)
                r = run_condition(task, cond, cfg, tmpdir, project_dir)
                results.append(r)
                print(json.dumps(r), flush=True)

    if args.out:
        with open(args.out, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

    print("\n=== SUMMARY ===", file=sys.stderr)
    print(f"{'task':<12} {'cond':<5} {'success':<8} {'prunes':<7} "
          f"{'tok_before':<11} {'tok_after':<10}", file=sys.stderr)
    for r in results:
        print(f"{r['task_id']:<12} {r['condition']:<5} "
              f"{str(r['success']):<8} {r['prune_events']:<7} "
              f"{r['tokens_before_run']:<11} {r['tokens_after_run']:<10}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
