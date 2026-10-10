"""A/B evaluation: 'Explain the agent loop' on the real harness repo.

Usage:
    python -m evals.run_ab --provider <name> --model <id> \
        --project-dir D:/projects/context-harness

Runs under:
  A (--no-prune): naive, append-only, truncate oldest at window
  B (default):    full harness (budget + janitor + deterministic prune)

What it does (Rob's manual test, automated):
  1. Fresh session.
  2. "Explain the agent loop." Model reads harness/loop.py and related
     files via tools, building real context from real tool output.
  3. Follow-ups ask it to dig deeper into specific files.
     Context grows; pruning triggers.
  4. Final: "Summarize what the agent loop does in 3 sentences."
     We check if the summary is coherent and mentions key concepts.

Metrics: tokens before/after, prune events, and whether the final
summary mentions core concepts (Loop, run_turn, budget, prune).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.config import load_config
from harness.context import Budget, count_tokens
from harness.loop import Loop, Session
from harness.assembly import assemble


TASK_ID = "explain-agent-loop"
TURNS = [
    "Explain the agent loop. Read harness/loop.py, focusing on the Loop "
    "class. What is its purpose?",
    "Now read harness/deterministic.py. What does the prune ladder do?",
    "Read harness/approvals.py. How does the Policy decide what to allow?",
    "Read harness/session.py. What is init_layout for?",
]
FINAL_QUESTION = (
    "Summarize in 3 sentences: what does the agent loop do, "
    "and how does it manage context?")
# Key concepts that should appear in a good summary.
EXPECTED_CONCEPTS = ["loop", "run_turn", "prune", "budget", "context"]


def run_condition(condition: str, cfg, tmpdir: Path,
                  project_dir: str) -> dict:
    from harness.providers import make_provider

    prune_enabled = (condition == "B")
    provider = make_provider(cfg, provider=cfg.provider, model=cfg.model,
                             base_url=cfg.base_url, api_key=cfg.api_key)
    # Low budget to guarantee pruning triggers.
    budget = Budget(hard=12_000, soft=8_000,
                    window=getattr(cfg, "context_window", None))
    prune_events = []

    def on_event(kind, data):
        if kind in ("prune-deterministic", "prune", "naive-truncate"):
            prune_events.append({"kind": kind, "data": data})

    loop = Loop(provider, budget, on_event=on_event,
                prune_provider=provider,
                prune_enabled=prune_enabled,
                prune_target=cfg.prune_target,
                prune_keep_tools=cfg.prune_keep_tools,
                prune_section_cap=cfg.prune_section_cap)

    sess_dir = tmpdir / f"eval-{TASK_ID}-{condition}"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess = Session(id=f"{TASK_ID}-{condition}", dir=sess_dir,
                   workdir=project_dir)

    def tok():
        return count_tokens(assemble(sess.dir))

    tokens_before = 0
    start = time.time()
    try:
        for i, turn in enumerate(TURNS):
            loop.run_turn(sess, turn)
            if i == 0:
                tokens_before = tok()
        answer = loop.run_turn(sess, FINAL_QUESTION)
        # Check: does the summary mention key concepts?
        low = (answer or "").lower()
        concepts_hit = sum(1 for c in EXPECTED_CONCEPTS if c in low)
        success = concepts_hit >= 3
    except Exception as e:
        answer = f"ERROR: {e}"
        success = False
        concepts_hit = 0
    wall = time.time() - start

    return {
        "task_id": TASK_ID,
        "condition": condition,
        "success": success,
        "concepts_hit": concepts_hit,
        "answer": (answer or "")[:300],
        "tokens_before_run": tokens_before,
        "tokens_after_run": tok(),
        "prune_events": len(prune_events),
        "wall_time_s": round(wall, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--out", default=None)
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
        for cond in ("A", "B"):
            print(f"Running {TASK_ID} condition {cond}...", file=sys.stderr)
            r = run_condition(cond, cfg, tmpdir, project_dir)
            results.append(r)
            print(json.dumps(r), flush=True)

    if args.out:
        with open(args.out, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

    print("\n=== SUMMARY ===", file=sys.stderr)
    for r in results:
        print(f"{r['task_id']} {r['condition']}: success={r['success']} "
              f"concepts={r['concepts_hit']}/5 "
              f"prunes={r['prune_events']} "
              f"tok {r['tokens_before_run']}->{r['tokens_after_run']}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
