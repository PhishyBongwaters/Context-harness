# Blank-slate assembly — specification (T0)

**Status:** spec, implements plan `docs/blank-slate-assembly-plan.md`
**Branch:** `feat/blank-slate-assembly`

## 1. What changes and why

Today `context.md` is an accumulating transcript the model curates with
read/write/edit. Accumulation plus model curation produced the failure
modes we actually hit: system-prompt text bleeding into the transcript
and getting re-injected every turn (recursive duping), and
read/read/read/reread edit cycles that cost more context than they save
(2026-10-08).

New model: the harness **assembles** the model's context from sources
every turn. The model never writes the transcript; it edits designated
source files with replace-only edits. Duping becomes impossible by
construction: there is exactly one copy of everything, owned by the
harness, and assembly is deterministic.

Non-goals: provider plumbing, TUI, prompt wording. Those are untouched.

## 2. Session layout

```
<session-dir>/
  prompt.md            # HARNESS-OWNED. Model may not edit.
  state.json           # HARNESS-OWNED. {"turn": N, "section_seq": M}
  index.md             # HARNESS-OWNED. Pointer map (the holy index).
  history.md           # MODEL-EDITABLE. Curated durable transcript.
  sats/
    facts.md           # MODEL-EDITABLE. One-liners.
    decisions.md       # MODEL-EDITABLE. One-liners.
    tasks.md           # MODEL-EDITABLE. One-liners.
  scratch.md           # HARNESS-OWNED. Current episode tool trace.
  archive/
    <stamp>-episode-t<NNNN>.md   # HARNESS-OWNED. Closed episodes.
  context.md           # HARNESS-OWNED build artifact: the assembled
                       # context, written fresh each turn for inspection.
                       # The model neither reads nor edits it: the content
                       # is already its injected messages.
```

`state.json`, `index.md`, `scratch.md`, `archive/` are created by the
harness on session init. `prompt.md` defaults to the current
`SYSTEM_PROMPT` text; a custom prompt file may be supplied at session
start. Everything is stdlib-only, Win11-clean paths
(`%APPDATA%`/`%LOCALAPPDATA%`) as today.

## 3. Per-turn assembly

Each turn the harness:

1. Reads `prompt.md` fresh → sent as the API **system** message.
   (Decision: prompt rides the system parameter, not the transcript.
   Rationale: provider flow unchanged; the transcript carries zero
   prompt text so the model cannot echo or duplicate it. Revisit only
   if a provider mishandles large system blocks.)
2. Assembles the transcript in fixed order:
   `sats/` (facts, decisions, tasks) → `history.md` → `scratch.md`.
   Rationale: stable knowledge first (primacy), working memory last
   (recency), conversation in the middle.
3. Writes the assembled text to `context.md` (inspectable artifact).
4. Parses it with the existing `parse_transcript` into API messages.
5. Appends the ephemeral usage note (`_note`, unchanged: sent, never
   stored).

The model sees exactly the assembled messages. There is no other
memory. What it saw last turn and what it sees this turn differ only
by what the harness assembled — never by silent truncation.

## 4. Stamps and section IDs (harness-owned)

The model references IDs; it never invents ordering or counts turns.

- `state.json.turn`: monotonic, incremented once per `run_turn`.
  Never derived from model output.
- History sections carry stamps in the header:
  `## user t0042`, `## assistant t0042`. The section regex
  (`_HEADER_RE` in `harness/context.py`) is extended to accept the
  stamp; the stamp is harness-written, preserved verbatim by the
  parser, and ignored on the wire (or surfaced — T3 decides; default
  ignored).
- `## tool <id>` sections keep provider call IDs, namespaced per
  episode in `scratch.md` (they never leave the episode file, so
  global uniqueness is not required).
- Satellite anchors follow the satellite proposal: `## <anchor>`
  headers inside sat files; pointers as `sats/facts.md#anchor`.
  Line numbers are never pointers; prose is never pointers.

## 5. Edit rules (model discipline)

The model edits sources with the **`edit` tool only**: exact
`old_string` → `new_string` replacement.

- `edit` fails loudly unless `old_text` matches **exactly once**.
  Zero matches → error naming the file. Two or more → error telling
  the model to include more surrounding context. (Current `edit_tool`
  silently replaces the first occurrence — that changes in T4.)
- `write` (whole-file overwrite) is **rejected for session sources**
  (`prompt.md`, `history.md`, `sats/*`, `scratch.md`, `index.md`,
  `state.json`, `context.md`, `archive/*`). It remains available for
  the model's own work files (code, notes outside the session dir).
- `read` on sources stays allowed for inspection. The banned pattern
  is read-modify-write cycles for context management, which the
  replace-only rule makes pointless.
- Edit gates live in the tool layer (`harness/tools.py`, extending the
  existing `_policy`/`_touches_context` pattern in `loop.py`): every
  `edit`/`write` call is checked against the source table before
  execution. Denied calls return a DENIED message, as approvals do
  today.
- Every accepted source edit emits the existing mechanical diff
  (sections changed + token delta, via `diff_transcripts`-style
  section diffing) and a pre-edit backup, exactly as context edits do
  today (`_backup_context`, `_execute_tool`).

Source table:

| file              | model read | model edit | harness writes |
|-------------------|------------|------------|----------------|
| prompt.md         | yes        | no         | yes (init)     |
| state.json        | no         | no         | yes            |
| index.md          | yes        | no         | yes            |
| history.md        | yes        | edit-only  | no             |
| sats/*.md         | yes        | edit-only  | no (init)      |
| scratch.md        | yes        | no         | yes            |
| archive/*         | yes        | no         | yes            |
| context.md        | yes        | no         | yes (per turn) |

## 6. Scratch lifecycle (tool working memory)

Tool calls are working memory with a TTL, not durable state.

- During a turn, the harness appends `## assistant` sections (with
  `tool-calls` fences, as today) and `## tool <id>` result sections
  to `scratch.md`. The model never edits this file.
- **Episode close:** an assistant reply with **no tool calls** closes
  the episode (this matches the existing `run_turn` completion
  condition). On close the harness:
  1. moves `scratch.md` → `archive/<YYYYMMDD-HHMMSS>-episode-t<NNNN>.md`,
  2. truncates `scratch.md` to empty,
  3. appends a cheap pointer to `history.md`
     (`## episode t<NNNN>` with archive path, turn range, tool-call
     count),
  4. **records the closing reply to `history.md` itself**
     (`## assistant t<NNNN>`) — H1. The model is never asked to decide
     what is durable; the conversation record is complete by
     construction,
  5. runs **history windowing** (H2) — see §6b.
- **Lookback:** the model may `read` any `archive/` file by path
  (discovered via `index.md`). Deliberate, visible cost — no silent
  bloat.
- Archive purging/sorting is explicitly deferred (out of hot context).
- Existing deterministic stages are re-homed, not removed:
  `dedupe_exact` runs on `scratch.md` after each tool result (as
  `_auto_dedupe` does today); `evict_oldest_tools` and `cap_sections`
  apply to `scratch.md` only.

## 6b. History windowing (H2, deterministic)

`history.md` is bounded by the harness, not by model summarization.
After every episode close, `window_history(sdir, cap_tokens)` groups
history sections by turn stamp; while the file exceeds the cap it
moves the oldest whole turns to
`archive/history-<date>-t<NNNN>-t<NNNN>.md` and leaves a
`## history-archive` pointer (archive path, turn range) in their
place. The newest turn is never archived; at least one stamped turn
is kept. Default cap: half the soft budget (`Loop(history_cap=...)`
overrides). No model calls, no judgment — windowing with lookback,
mirroring the episode design. The curation turn therefore never needs
to "summarize old history"; its remaining job is sats hygiene.

## 7. Budget and prune turns, reworked

- Soft/hard budget and the per-turn meter are unchanged.
- Hard breach no longer triggers "prune the transcript" (there is no
  transcript to prune). Instead:
  1. harness runs the deterministic ladder on `scratch.md`;
  2. if still over, the model gets a **curation turn**: edit-only on
     `history.md`/`sats/*.md` until the next assembled request fits.
     Same loud-failure contract as today's prune turn
     (`MAX_PRUNE_ATTEMPTS`, no silent truncation).
- `prune_turn` in `loop.py` is reworked to this shape (landed in T6,
  pulled forward from the T7/T9 slot).

## 8. Index format (holy, harness-owned)

`index.md` — the model reads it, only the harness writes it:

```markdown
# Index

## satellites
- sats/facts.md
- sats/decisions.md
- sats/tasks.md

## episodes
- archive/20261009-093012-episode-t0042.md  (turns 38-42, 14 tool calls)
```

The verifier (satellite proposal §Integrity) checks: every pointer
resolves, no dangling refs. A failed check is a loud error. Purity is
mechanical because the model cannot write this file.

## 9. Migration

Clean cutover for new sessions. For existing single-file `context.md`
sessions, T10 ships a one-shot migration: parse old sections —
`## user`/`## assistant` → `history.md` (stamped in order),
`## tool` sections → one `archive/` file, pointers into `index.md`.
Best-effort, loud on ambiguity, never silent.

## 10. Test plan

Red-first per task, existing suite stays green throughout:

- `tests/test_assembly.py` — order, determinism, byte-identical
  re-assembly from unchanged sources.
- `tests/test_stamps.py` — monotonic turns, stamp parsing round-trip,
  model cannot forge stamps (gate rejects).
- `tests/test_edit_gates.py` — 0-match and 2-match `edit` failures,
  `write` rejected on sources, allowed off-session.
- `tests/test_scratch.py` — episode close archives + truncates +
  indexes; lookback read works; dedupe/evict/cap re-homed to scratch.

## 11. Decisions (resolved 2026-10-09, Rob)

1. Prompt rides the system parameter (§3). Overturn requires a reason,
   not a feeling.
2. The model never edits `prompt.md`. Harness-owned, full stop.
3. Episode-close pointer: a `## episode t<NNNN>` section in
   `history.md` holding the archive path, turn range, and tool-call
   count, wired to the model as a plain user-role message.
4. Stamps are stripped on the wire. Harness bookkeeping only; recency
   is communicated by section order.
