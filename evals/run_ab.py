"""A/B retention evaluation runner — real multi-turn sessions.

Usage:
    python -m evals.run_ab --provider <name> --model <id> [--tasks needle-01]

Runs each task under:
  A (--no-prune): naive, append-only, truncate oldest at window
  B (default):    full harness (budget + janitor + deterministic prune)

How it works (like a real session):
  1. Fresh session, empty history.
  2. Turn 1: user asks the model to investigate something. The model
     uses tools (read/exec) and encounters the needle naturally in
     tool output — just like a real debugging session.
  3. Turns 2-N: user asks follow-up questions requiring more tool
     calls. Context grows to 30k+ tokens from real tool output.
  4. Pruning triggers (condition B) or naive truncation (condition A).
  5. Final turn: user asks about the needle from early in the session.
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

from evals.tasks.needle_tasks import TASKS
from evals.quiz import check_answer
from harness.config import load_config
from harness.context import Budget, count_tokens
from harness.loop import Loop, Session


def setup_task_files(task: dict, workdir: Path) -> None:
    """Create the fake project files the model will investigate.

    The needle is planted in one of the files, encountered naturally
    when the model reads it during investigation.
    """
    proj = workdir / f"eval-{task['id']}"
    proj.mkdir(parents=True, exist_ok=True)
    # Charter contains the needle.
    (proj / "CHARTER.md").write_text(
        f"# Project Charter\n\n{task['needle']}\n\n"
        f"Status: active\nTeam: 8 engineers\n",
        encoding="utf-8")
    # Lots of other files for the model to sift through.
    for i in range(30):
        (proj / f"module_{i:02d}.py").write_text(
            f'"""Module {i}: utility functions."""\n\n'
            f"def helper_{i}(x):\n"
            f'    """Do something with x."""\n'
            f"    return x * {i + 1}\n\n"
            f"CONFIG_{i} = {{'timeout': {10 + i}, 'retries': 3, "
            f"'endpoint': 'https://api{i}.example.com/v1'}}\n",
            encoding="utf-8")
    # Verbose log file for context bulk.
    log_lines = []
    for i in range(500):
        log_lines.append(
            f"[2026-10-10 06:{i//60:02d}:{i%60:02d} INFO] "
            f"request {i}: GET /api/v1/resource/{i} -> 200 "
            f"({20 + (i % 50)}ms)")
    (proj / "app.log").write_text("\n".join(log_lines), encoding="utf-8")
    return proj


def run_condition(task: dict, condition: str, cfg, tmpdir: Path) -> dict:
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
    sess_dir = tmpdir / f"eval-{task['id']}-{condition}"
    sess_dir.mkdir(parents=True, exist_ok=True)
    proj_dir = setup_task_files(task, tmpdir / "work")
    sess = Session(id=f"{task['id']}-{condition}", dir=sess_dir,
                   workdir=str(proj_dir))

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
        # Turn 1: investigate the project. Model reads CHARTER.md (needle)
        # and other files via tools. Context grows from real tool output.
        a1 = loop.run_turn(
            sess,
            f"Investigate the project in {proj_dir}. Start by reading "
            f"CHARTER.md, then skim a few module files and the log to "
            f"understand the structure. Summarize what this project does.")
        tokens_before = count_tokens(sess.context.load())

        # Turns 2-4: more investigation, growing context.
        a2 = loop.run_turn(
            sess,
            "Now dig into the modules. Read at least 10 module files and "
            "report any interesting patterns in the helper functions.")
        a3 = loop.run_turn(
            sess,
            "Check the log file for errors or warnings. Summarize what "
            "you find.")
        a4 = loop.run_turn(
            sess,
            "Look at the remaining modules you haven't checked yet.")

        # Final turn: the retention quiz. Ask about the needle from turn 1.
        answer = loop.run_turn(sess, task["final_question"])
        success = check_answer(answer or "", task["needle_answer"])
    except Exception as e:
        answer = f"ERROR: {e}"
        success = False
    wall = time.time() - start

    # Did the needle survive pruning?
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
    ap.add_argument("--tasks", default=None,
                    help="Comma-separated task IDs (default: all)")
    ap.add_argument("--out", default=None, help="JSONL output path")
    args = ap.parse_args()

    cfg = load_config()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model

    wanted = set(args.tasks.split(",")) if args.tasks else None
    tasks = [t for t in TASKS if not wanted or t["id"] in wanted]

    import tempfile
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for task in tasks:
            for cond in ("A", "B"):
                print(f"Running {task['id']} condition {cond}...",
                      file=sys.stderr)
                r = run_condition(task, cond, cfg, tmpdir)
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
