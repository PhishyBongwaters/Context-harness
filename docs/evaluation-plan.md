# Evaluation plan — satellite context

**Status:** plan (2026-10-07)
**Goal:** produce honest numbers for the engineering layer — the parts the
CLM paper doesn't cover: budget-legible pruning, the janitor pattern,
completion-triggered pruning, satellite files with a verified index.

Rule zero, from hard experience: measured, not estimated. Same tasks on
both sides, token counts from real payloads, analysis from committed
data. A number neither side can reproduce is worse than no number.

## Claims → metrics

| # | Claim | Metric |
|---|-------|--------|
| 1 | Curated context uses fewer tokens per task than history-dump | `input_tokens_total` per task, paired |
| 2 | …without losing task success | `task_success` rate, paired |
| 3 | Pruning preserves the signal, not just the shape | retention-quiz score after prune events |
| 4 | Completion-triggered pruning beats budget-panic pruning | tokens used per completed task; prune latency (turns between done-ness and compression) |
| 5 | The index stays pure | verifier catch rate on injected faults; dangling-pointer count in real runs (must be 0) |
| 6 | Satellite layers scale where monolithic context doesn't | max task length (input-volume ÷ window) completed successfully |

## Conditions

- **A — naive:** append-only transcript, truncate oldest at the window.
  The "stupid and lazy" baseline. (Simulate with pruning disabled if no
  naive mode exists.)
- **B — harness today:** budget + janitor + deterministic prune stages.
- **C — harness + satellites:** B plus facts/decisions/tasks layers,
  completion-triggered prune, verified index.

Compare A→B (does the current harness already win?) then B→C (do the
satellites add anything?). A→B is runnable today.

## Task suite

Two task families, 10–20 tasks total to start:

1. **Needle + distractor (ContextBench-style).** Long tool-output-heavy
   tasks where the correct answer depends on information from early
   turns buried under distractors. Directly tests whether pruning keeps
   the signal. Input volume up to ~10× the window.
2. **Realistic coding tasks.** "Fix this bug" / "add this feature" on a
   mid-size repo, judged by tests passing. Messy, real tool output.

Every task needs: a fixed starting state, an unambiguous success
criterion, and (for family 1) ground-truth answers.

## Metrics — exact definitions

- `input_tokens_total`: sum of prompt tokens across **all** model calls
  in the task, measured with the pinned tokenizer (tiktoken), not
  provider-reported counts. This is the cost driver.
- `output_tokens_total`: same for completions.
- `task_success`: binary. Family 1: answer matches ground truth.
  Family 2: test suite green.
- `turns_to_complete`: agent turns until success or give-up.
- `prune_events`: count per task; tokens before/after each event.
- `retention_score`: see quiz protocol below.
- `wall_time`: informational only (not a claim).

## Retention-quiz protocol (claim 3)

The sharpest test of prune quality:

1. Run a task until a prune event fires.
2. Freeze the pre-prune context. Extract N ground-truth facts from the
   region that got purged (human-written, or model-extracted then
   human-verified — 5–10 per task).
3. Give a fresh model instance **only the pruned file** and quiz it.
4. `retention_score` = fraction answered correctly.

A prune that keeps the shape but drops the signal scores low. Report
the score distribution, not just the mean.

## Index-verifier protocol (claim 5)

- **Fault injection:** take 10 real index pointers, break them (delete
  the anchor, rename the file), verifier must flag 10/10.
- **Real runs:** after every task, run the verifier. Dangling-pointer
  count must be 0 across the whole suite. Any nonzero is a bug, not a
  statistic.

## Instrumentation checklist

Already in the harness: per-turn usage tracking (`usage.py`), debug
JSONL log, context-diff token deltas, prune event emission.

To build:
- [ ] per-task rollup: task id → all metrics above, one JSON line per task
- [ ] prune event log with tokens-before/after + trigger (budget vs completion)
- [ ] retention-quiz runner script (freeze → quiz → score)
- [ ] verifier fault-injection mode

## Run protocol

- Same model, same temperature (0), same task order (randomized once,
  reused across conditions).
- Same starting state per task (fresh session dir, reset repo).
- ≥2 reps per task per condition (model variance is real).
- Commit the raw per-task JSON before any analysis. Analysis reads the
  committed data — never the other way around.

## Analysis & reporting

- Paired comparison per task (C vs B on the *same* task), then aggregate.
- Report per-task deltas in a table plus median/mean. One hero number
  is fine for the abstract; the table is what makes it trustworthy.
- Report failures honestly: tasks where C lost to B are findings, not
  embarrassments.
- Never present a number that can't be recomputed from the committed
  run logs.

## Phased plan

- **Phase 0 — instrument.** Per-task rollup + prune event log. (Days.)
- **Phase 1 — A→B numbers.** Retention quiz on the current janitor +
  tokens-per-task vs naive. Runnable now; validates the harness story
  before satellites exist.
- **Phase 2 — build satellites**, then B→C numbers.
- **Phase 3 — write it up.** Numbers first, paper second.

## Minimal first experiment (this week)

Run 5 needle+distractor tasks under condition B. For each prune event,
run the retention quiz. Output: one table — task, prune events, tokens
recovered, retention score. That single table is the first real number
this project has ever produced about prune quality, and it's the
foundation everything else stands on.
