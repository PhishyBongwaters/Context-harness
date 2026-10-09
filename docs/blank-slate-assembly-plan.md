# Blank-slate assembly plan

Pivot (2026-10-09): from accumulating mutable `context.md` to deterministic
per-turn assembly. The harness builds the model's context from sources every
turn; the model edits sources via replace-only edits; tool traces live in a
harness-owned scratch file with per-episode archival.

**Status (2026-10-09): all tasks T0–T12 landed on
`feat/blank-slate-assembly`, each pushed and verified on origin.
Suite: 367 passed, 31 skipped; the only 3 failures are pre-existing
`test_tui_bridge` cases that fail identically on `main` (env-specific:
`rich` not installed here, so ANSI formatting falls back to plain
text). The spec §7 prune rework landed in T6 (pulled forward from its
T7/T9 slot).

This kills the system-prompt/transcript recursive-dupe class by construction:
the prompt is never stored in the transcript.

## Task list

### T0 — Assembly spec (do first, everything references it)
Write the spec: source file layout, assembly order, ID/stamp scheme, edit
rules, scratch lifecycle, episode-close definition, archive format. Keep it
short; code follows the spec, not the other way around.

### Phase 1 — Foundation
- **T1 — Session layout.** On-disk session dir: `prompt.md`, `history.md`,
  `sats/`, `scratch.md`, `archive/`. Win11-clean paths (`%APPDATA%` /
  `%LOCALAPPDATA%`), stdlib-only.
- **T2 — Harness-owned stamps.** Monotonic turn counter + stable section IDs
  (`## sat:<name>#t<NNN>` style gates). Persisted in session state the model
  cannot edit. Model references IDs; it never invents ordering or counts.
- **T3 — Assembly function.** Build per-turn context text from sources in
  fixed order: prompt → sats → history → scratch. Red-first tests with
  fixtures. (Reworks `harness/context.py`; `parse_transcript` stays the
  parser for the assembled output.)

### Phase 2 — Edit discipline
- **T4 — Replace-only edit tool.** Exact `old_string`/`new_string`; harness
  fails loudly on 0 or 2+ matches. Wire in the existing mechanical diff
  (`diff_transcripts`) so every edit reports token delta. No model
  read-modify-write cycles for context management (the 2026-10-08
  read/read/read/reread incident is the anti-pattern).
- **T5 — Edit-gate rules.** Which sources the model may edit (sats, history)
  vs harness-only (prompt assembly, stamps, scratch lifecycle). Spec-level,
  enforced in `harness/tools.py`.

### Phase 3 — Scratch lifecycle
- **T6 — Tool traces to scratch.** Tool calls/results append to `scratch.md`,
  not inline in the assembled context. Same `## tool <id>` section format so
  existing parsing/dedupe logic carries over. Also pulls forward the spec
  §7 prune rework (was slated T7/T9): run_turn assembles per step, the
  deterministic ladder runs on scratch.md, and prune turns are edit-only
  curation turns on history.md/sats (no gate bypass). User messages are
  stamped into history.md via the harness-owned turn counter.
- **T7 — Episode close.** Assistant reply with no further tool calls closes
  the episode: harness archives `scratch.md` to `archive/<date>-<turn>.md`,
  clears scratch, records the archive path as a pointer in history.
- **T8 — Archive lookback.** Agent can read an archived episode by path on
  demand. Deliberate, visible cost instead of silent bloat.

### Phase 4 — Integration
- **T9 — Parser verification.** `parse_transcript` against assembled output
  (multi-source, stamps, scratch sections). Mostly exists; verify + tests.
- **T10 — Migration.** Fresh sessions use the new layout. Decide: migrate
  old `context.md` files or clean cutover.
- **T11 — Docs.** README describes the new model: what the harness owns,
  what the model may edit, the episode lifecycle.

### Phase 5 — Verification
- **T12 — Suite green.** Full suite green, red-first tests per task above.
  No announce until pushed (standing policy).

## Parallelization notes
- T1, T2 can parallelize after T0 (spec) lands.
- T4 can parallelize with Phase 1 after T0.
- T6 needs T1+T3. T7 needs T6. T8 needs T7.
- T9–T11 need their phases done. T12 last.
