# Satellite Context — proposal

**Status:** proposal (2026-10-07)
**Prior art:** "Context Language Models" (Shao et al., arXiv 2609.37725, 2026-09-29) — independently converged on the same core loop.

## The problem

Chat-style agents dump the entire history per message. It works until the
window fills, then the harness panic-summarizes or truncates — usually
dropping the task itself. Summary is lossy and one-directional: you can
never un-summarize.

Tricorder attacked a adjacent problem (codebase too big for context) with
a precomputed structural map. It worked, but the map itself bloated —
a static, tool-owned artifact whose idea of "important" froze at index
time.

## Core insight

Treat context as a **document**, not a stream. Streams need garbage
collection, which models are bad at. Documents need editing, which is
what they're trained to do. The model curates its own working memory
every turn: keep, compress, reorganize, discard.

## The four layers

```
┌─────────────────────────────────────────────────────┐
│ 1. WORKING LAYER — context.md (+ live transcript)   │  ephemeral
│    Full diagnosis, script blocks, tool output.       │  purged freely
├─────────────────────────────────────────────────────┤
│ 2. CURATED LAYER — satellite files                   │  one-liners
│    facts.md · decisions.md · tasks.md · hypotheses.md │  model-written
├─────────────────────────────────────────────────────┤
│ 3. ARCHIVE LAYER — dated backups                    │  addressable
│    Purged detail, saved — never lost, rarely loaded. │
├─────────────────────────────────────────────────────┤
│ 4. THE INDEX — map section in context.md             │  HOLY
│    Pointers to everything above. Mechanically pure.  │
└─────────────────────────────────────────────────────┘
```

**Working layer.** Today's context.md plus the live transcript. Full
diagnosis, script blocks, self-conversation. Purged aggressively — this
layer is allowed to be messy because nothing in it is load-bearing.

**Curated layer.** Four satellite files, one-liners only:

- `facts.md` — world state discovered ("auth retry bug is in `login()`, not the token refresh")
- `decisions.md` — the *why* that evaporates ("chose X over Y because Z; rejected W after test T failed")
- `tasks.md` — what's pending, what's done
- `hypotheses.md` — half-formed ideas, unproven suspicions ("I think the
  latency is DNS but haven't verified"). Prune ruthlessly: promote to
  facts when confirmed, delete when disproven. This is the messy middle
  between scratch and fact — without it, hunches either die in scratch
  or get prematurely promoted to facts.

Findings fold into facts. The ontology stays small on purpose: eight
satellite files would reinvent the bloat with extra steps.

**Archive layer.** When the working layer is purged, the raw material
goes to dated backup files — never deleted, almost never loaded. Every
curated one-liner may carry a pointer to the backup chunk it distills,
so detail can be revisited and refreshed on demand.

**The index.** A map section in context.md pointing at everything above.
This is the holy layer: its purity is *mechanical*, not aspirational.

## Pointer format

Pointers are `##` section anchors, not line numbers and not prose:

```
facts.md#auth-retry-bug
decisions.md#chose-x-over-y
archive/2026-10-07-turns-12-18.md#session-auth
```

Line numbers rot on the first edit. Prose pointers ("the auth section")
rot on the fifth. Anchors survive as long as the section lives — and
section renames are the one edit the verifier checks explicitly.

## Integrity: model curates, machine verifies

The model writes one-liners and updates pointers. Deterministic code
verifies, like a foreign-key constraint:

- every pointer resolves (file exists, anchor present)
- no dangling refs, no orphaned archive chunks
- a failed check is a loud error, not silent rot

The index stays pure because impurity is unrepresentable — not because
the model is disciplined.

## Prune triggers

Two, not one:

1. **Budget-driven (janitor).** Today's mechanism: over budget → prune
   turn. Unchanged, still the backstop.
2. **Completion-triggered (new).** "Tests pass → drop the cruft." When a
   task completes green, the harness proposes compressing the working
   trace into curated one-liners *immediately*, rather than waiting for
   budget pressure. This is semantic pruning, and it's stronger than
   everything the janitor does — first-class turn phase, not a prompt
   nudge.

## Tricorder relationship

Tricorder's map is demoted from persistent artifact to **transient
scaffold**. The agent explores with Tricorder (fast structural queries),
distills findings into its own curated files, and deletes the map. The
tool does navigation; the model does curation. Neither carries the
other's weight. Map bloat dissolves because retention becomes the
model's decision, made per turn, instead of the tool's output frozen
at index time.

## The 80k local-model problem

Pulling 80k tokens into a small local model to prune is brutal — but the
satellite structure is itself the map-reduce answer. You never prune
80k at once; you prune `facts.md` (10k) while `context.md` stays a 2k
map. Hierarchical pruning falls out of the file layout instead of
needing a clever algorithm. What remains is a money problem (bigger
local model), not a project problem.

## Decisions vs. reasoning

We do **not** store raw reasoning traces (3–10x the response, mostly
noise — re-creates the bloat one level down). We store *decisions*: the
conclusions of reasoning that the transcript doesn't otherwise record.
One cheap distillation per turn — "what did I decide, what did I
reject?" — is the janitor's easiest job. Local R1-style models expose
`<think>` traces directly, which is an advantage the API models don't
have.

## Open questions

- Exact `##` anchor conventions and rename protocol.
- Whether `tasks.md` subsumes the existing session/turn tracking.
- Completion-triggered prune UX: automatic, proposed, or per-task opt-in?
- Verifier placement: turn phase, CI-style check, or both?
- Migration path for existing single-file `context.md` sessions.
