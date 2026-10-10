# A/B Retention Eval

**The eval keeps us honest.** It runs Rob's manual test — "explain the
agent loop" on the real codebase — under two conditions and measures
whether pruning preserves what the model needs.

## Quick start

```powershell
python -m evals.run_ab --provider llama.cpp --model Qwen `
    --project-dir D:/projects/context-harness
```

## What it does

1. Fresh session, empty history.
2. Turn 1: "Explain the agent loop." The model reads `harness/loop.py`
   and related files via tools, building real context from real output.
3. Follow-ups dig into `deterministic.py`, `approvals.py`, `session.py`.
   Context grows; pruning triggers.
4. Final: "Summarize what the agent loop does in 3 sentences."
   Checks if the summary mentions core concepts
   (`loop`, `run_turn`, `prune`, `budget`, `context`).

Runs twice:
- **A:** naive append-only, truncate oldest at window (`--no-prune`).
- **B:** full harness — budget + janitor + deterministic prune.

## Flags

| Flag | Purpose |
|------|---------|
| `--project-dir` | **Required.** Real project for the model to investigate. |
| `--prompt` | Custom initial prompt (replaces "Explain the agent loop"). |
| `--transcript-dir` | Save raw transcripts as `{task}-{condition}.md` for independent verification. Don't trust harness numbers — count them yourself. |
| `--out` | Write JSON results to file (one per line). |
| `--provider`, `--model` | Override config. |

## Metrics

| Field | Meaning |
|-------|---------|
| `success` | Final summary hit ≥3/5 core concepts. |
| `concepts_hit` | How many of the 5 concepts appeared (0–5). |
| `peak_tokens` | Max tokens seen by any prune event (the 30k you see in the GUI). |
| `post_prune_tokens` | Tokens after the last prune (the 4k). |
| `prune_events` | Number of prune/naive-truncate events. |
| `transcript` | Path to saved raw transcript (if `--transcript-dir` given). |

`peak_tokens` and `post_prune_tokens` come from the prune events
themselves (`tokens_before`/`tokens_after` in the deterministic
report), not from measuring after `run_turn` (which would miss the
peak because pruning happens at turn start).

## Design notes

- The model **participates** — no pre-loaded fake history. It builds
  context by reading real files, like Rob does manually.
- Budget is intentionally low (hard=12k, soft=8k) to guarantee pruning
  triggers. Reading 4-5 harness files is ~20k tokens.
- The `--transcript-dir` output is the unmolested assembled transcript.
  Verify our numbers with your own tokenizer.

## History

- 2026-10-07: `docs/evaluation-plan.md` — full A/B/C plan (plan only).
- 2026-10-08: First `run_ab.py` — 5 synthetic needle+distractor tasks.
- 2026-10-10: Rewritten to real multi-turn on the actual project
  ("Explain the agent loop"), replacing synthetic needles per Rob's
  manual workflow. Added `--prompt`, `--transcript-dir`, and
  peak/post-prune measurement from prune events.
