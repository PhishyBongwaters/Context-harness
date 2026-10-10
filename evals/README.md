# A/B Retention Evaluation

Minimal first experiment from `docs/evaluation-plan.md` (Phase 1: A→B).

## What it does

Runs 5 needle+distractor tasks under two conditions with the **same model**:

- **A (`--no-prune`):** naive — append-only, truncate oldest at window
- **B (default):** full harness — budget + janitor + deterministic prune

Each task buries a key fact early under 20 distractor turns, then asks
for it at the end. Measures whether pruning keeps the signal.

## Run it

```bash
# Full A/B (needs a configured provider — API key or local server)
python -m evals.run_ab --provider <name> --model <id> --out results.jsonl

# Single task, both conditions
python -m evals.run_ab --provider nvidia --model <id> --tasks needle-01
```

Output: one JSON line per task-condition on stdout, plus a summary
table on stderr. Use `--out` to save the JSONL for analysis.

## Metrics per task-condition

- `success`: did the final answer contain the needle?
- `prune_events`: how many prune/naive-truncate events fired
- `tokens_before_run` / `tokens_after_run`
- `quiz`: per prune event — tokens before/after, whether needle survived
- `wall_time_s`

## Interpreting

A→B wins if B has higher `success` rate with fewer total input tokens.
The `quiz` array shows per-event signal preservation — the sharpest
test of prune quality.
