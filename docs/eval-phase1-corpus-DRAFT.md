# Phase 1 minimal experiment — task corpus & quiz protocol (DRAFT)

Status: draft, 2026-10-07. Not started. Complements `evaluation-plan.md`
("Minimal first experiment (this week)"): 5 needle+distractor tasks under
condition B, retention quiz per prune event, one output table.

The plan's open checklist item this unblocks: the retention-quiz runner
needs tasks with **known-by-construction ground truth**. This doc supplies
them.

## Design choice: deterministic corpus, ground truth known a priori

The plan says quiz facts may be "human-written, or model-extracted then
human-verified." Generating the corpus from a **fixed seed** is better:
the ground truth is whatever we wrote into the generator, so no
extraction and no human verification step. The generator is the oracle.

Second choice: **shrink the budget instead of generating 10x distractors.**
The plan's family-1 tasks say "input volume up to ~10x the window." But
the harness exposes `--budget-hard` / `--budget-soft`. Running the task
with a small `--budget-hard` forces the same hard-breach → prune-only
turn mechanics at a fraction of the token cost. Prune quality is what
we're measuring, not the model's ability to read 800k tokens.

Calibrate per task: start `--budget-hard` at ~12k, raise until the agent
reliably finishes the task but at least one hard-breach prune fires.
Record the budget used per task in the results table.

## Corpus generator (to write: `tools/gen-eval-corpus.py`)

Fixed seed (e.g. 42). For each of the 5 tasks, emits a session working
dir with the needle files + distractor files. All distractor files use
the same vocabulary as the needles (similar names, similar schemas) but
never contain the correct answer for the task question.

| Task | Needle (early turns) | Distractors (bulk) | Question (success criterion) |
|------|----------------------|--------------------|------------------------------|
| T1 alert-routing | `services/<3-of-40>/routing.yaml` list `alert-api-latency-p99` with runbook URL | 40 service dirs; 37 routing.yaml mention the alert with *different* routes/runbooks | Which services own `alert-api-latency-p99` and what is the runbook URL? |
| T2 flag-state | `flags/billing-v3-rollout.yaml`: rollout 10%, kill-switch `billing-kill`, owner `team-payments` | 50 flag YAMLs with similar names (`billing-v2`, `billing-v3-canary`, …) and different states | Rollout %, kill-switch name, owner team for `billing-v3-rollout` |
| T3 shard-map | `shards/manifest.json`: tenant `acme-corp` → `db-shard-07`, replica lag threshold 500ms | 12 shard dirs + manifests mapping other tenants; similar IDs | Which shard holds `acme-corp` and what is the lag threshold? |
| T4 incident | `incidents/INC-4821.md`: root-cause service `auth-gateway`, fix commit `9f31ac2`, deploy tag `v2.14.7` | 30 incident reports, same template, different IDs/services/commits | Root-cause service, fix commit, deploy tag for INC-4821 |
| T5 rotation | `accounts/svc-deploy-07.json`: credential expires 2026-10-09, runbook `runbooks/rotate-deploy.md` | 60 account JSONs with expiry dates (all later or much earlier) | Which account expires soonest and what is the rotation runbook? |

Task prompt pattern (read verbatim to the agent, no hints):
"On-call handoff. <question> Explore the repo and answer."

Quiz facts per task (the retention targets): the question's answers
plus 3–4 secondary facts from the needle files (owner team, threshold,
tag, runbook path). 6–8 facts per task; aim for the plan's 5–10 range.

## Run procedure (per plan's run protocol)

1. Fresh session: `python -m harness --new` per task per rep (≥2 reps,
   same model, temp 0, same calibrated `--budget-hard`, seed fixed so
   starting state is identical).
2. Let the agent answer. Log prune events: the harness already prints
   tokens recovered per prune turn, and writes `context.pre-prune-<ts>.bak`
   — **the freeze step is free**: the pre-prune backup *is* the frozen
   pre-prune context.
3. Quiz: fresh session, paste only the post-prune `context.md`, ask the
   6–8 quiz questions one per turn (or as a batch — decide once, keep
   constant), score against the generator oracle.
4. `retention_score` = fraction correct, per prune event.

## Output table (the one table this experiment produces)

| task | rep | budget_hard | prune_events | tokens_recovered_total | retention_score |
|------|-----|-------------|--------------|------------------------|-----------------|

Plus the score distribution (per-question breakdown), not just the mean —
a prune that drops one fact type systematically is the interesting
finding.

## What stays manual vs. what to script

- Manual today: calibration of `--budget-hard`, the quiz sessions
  (copy-paste), scoring (read answers, diff against oracle).
- Worth scripting if Phase 1 repeats: the generator (`tools/gen-eval-corpus.py`),
  the quiz driver (feed pruned context + questions, record answers), the
  rollup (one JSON line per task — the plan's per-task rollup item).

## Open decisions (Rob's call)

1. **Model for the quiz:** same model as the agent (keeps the comparison
   clean), or a stronger model (isolates prune quality from quiz-taker
   ability)? Plan implies same; I'd go same model, temp 0, and note it.
2. **Janitor config during the experiment:** default janitor (same model)
   vs. a cheaper janitor model — the janitor *is* the thing being measured,
   so its config is part of the condition. Record it, don't vary it.
3. **Batch vs. one-per-turn quiz delivery:** pick one, hold constant across
   all tasks and reps.
