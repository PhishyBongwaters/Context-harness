# CLM prior-art notes — DRAFT (uncommitted, 2026-10-07)

Research brief on "Context Language Models" (Shao et al., arXiv 2609.37725,
2026-09-29) vs. the satellite-context proposal. Purpose: honest positioning —
what's genuinely theirs, what's genuinely ours, and where the numbers need to
land before any write-up.

## What CLM is (paper facts)

- Authors: Rulin Shao, Shannon Zejiang Shen, Pang Wei Koh + 10 co-authors —
  UW, Meta Superintelligence Labs, MIT, Trillium Labs. Submitted 2026-09-29.
- Core move: stop treating context as append-only. The context is a **file**;
  the model edits it with ordinary commands (append, delete, rewrite) and
  edits are synced back into the model's context before the next step.
- Behaviors observed zero-shot: tracker tables for multi-agent collaboration,
  reusable context-management functions — strategies no hand-written framework
  had.
- Code released: `facebookresearch/context-language-models`, CC BY-NC 4.0.
- The paper's own safety section warns the same freedom lets a model **plant
  instructions for itself**.

## What CLM claims (paper-reported; no third-party replication yet)

- Qwen3.6-27B zero-shot CLM: **59.4% on BrowseComp-Plus vs 53.4%** for
  Codex-style summarisation, **21.5% fewer FLOPs**.
- 12-hour EdgeBench: 5% higher scores, 59% fewer FLOPs.
- 24-hour multi-repo agent-swarm task: 65% greater improvement, same compute.
- Online RL recipe: lifts Qwen3.5-9B on BrowseComp-Plus by 47.6% with 12%
  fewer FLOPs (i.e. the editing behavior is *trainable*, not just promptable).
- Co-designed Suffix Cache Reuse serving layer: 35% less server-side compute
  vs standard SGLang at matched performance.
- Independent reading (dev.to, 2026-10-01): the CLM context file is
  **per-task ephemeral — thrown away when the task ends**. Whatever the agent
  needs next week is still the builder's problem.

## Where satellites genuinely differ (the open territory)

The core loop — model edits its own context as a document — is published and
prior. Everything below is what the paper does *not* cover, and it's exactly
the engineering layer our evaluation plan targets:

1. **Model curates, machine verifies.** CLM trusts the model's edits (and its
   safety section flags the risk). We split the job: model writes one-liners
   and pointers; deterministic code enforces foreign-key-style integrity
   (every pointer resolves, no dangling refs, no orphaned archive chunks).
   The index is pure because impurity is *unrepresentable*, not because the
   model is disciplined. This is our direct answer to their safety warning.
2. **Four layers, not one file.** CLM has a single editable context file.
   We have working / curated / archive / index, with one-liner satellites
   (`facts.md`, `decisions.md`, `tasks.md`) and dated archive backups with
   back-pointers. Hierarchical pruning falls out of the file layout.
3. **Completion-triggered pruning.** CLM prunes under the same pressure the
   paper's baselines face (window filling). Our second trigger — "tests pass
   → compress the trace into one-liners *now*" — is semantic, not
   budget-driven, and the paper has no equivalent.
4. **The janitor + deterministic prune ladder.** Budget-legible,
   phase-gated pruning with usage tracking is production machinery the paper
   never discusses.
5. **The 80k local-model problem.** CLM's numbers are on 27B/9B models with
   RL training. Our target is a 16GB-VRAM local model pruning 80k of context
   hierarchically (prune `facts.md` at 10k while `context.md` stays a 2k map).
   Different optimization target, different constraints.
6. **Decisions, not reasoning traces.** We deliberately do *not* store raw
   `<think>` traces (3–10x the response, mostly noise); we store the
   conclusions the transcript doesn't otherwise record. The paper doesn't
   address the trace-bloat recursion.

## Honest positioning (candidate one-liner)

"CLM proved the core loop — a model editing its own context beats
hand-written compaction. Satellites are the production layer CLM doesn't
cover: a verified index, completion-triggered pruning, and hierarchical
curation that survives on a 16GB local model."

## What would strengthen the claim

- The retention-quiz table (Phase 1) is the first number CLM's authors never
  published about *their own* loop: prune quality measured as signal
  retention, not just token recovery. If our janitor scores well there, it's
  a result about the whole family, and it's ours.
- Index-verifier fault-injection results (10/10 + zero dangling pointers
  across real runs) turn "the index is holy" from framing into a measured
  property.

## Open questions

- Is our curated layer per-task (like CLM's file) or persistent across
  tasks/sessions? If persistent, that's a further differentiator — and it
  needs the verifier even more.
- Community implementations already exist (`pi-clm` extension for the Pi
  coding agent, Baize's CLM-inspired projection). Worth a skim to see which
  of our six points they've already stumbled into.

## Sources

- Paper: https://arxiv.org/abs/2609.37725 (arXiv 2609.37725, 2026-09-29)
- Code: https://github.com/facebookresearch/context-language-models (CC BY-NC 4.0)
- https://dev.to/gaurav_dadhich/context-language-models-what-the-uw-and-meta-paper-changes-for-agent-builders-and-what-it-leaves-40a7 (2026-10-01)
- https://aiweekly.co/alerts/uw-meta-paper-language-models-that-edit-their-own-context (notes: all numbers paper-reported, no third-party replication yet)
- https://interviewstack.io/blog/context-language-models-explained
- https://dev.to/rebornace/context-as-a-file-baizes-clm-inspired-long-conversation-projection-50n9
- https://www.youtube.com/watch?v=Bgtr1Ue40Jo (pi-clm hands-on, prefix-cache gotchas)
