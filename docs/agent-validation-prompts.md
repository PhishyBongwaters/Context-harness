# Agent validation prompts

Adversarial prompts to run against a real agent in the harness, each
designed to trigger one bad behavior the cleaning mechanisms should
prevent or bound. Run each in a **fresh session** (`--new`) unless
noted. After each, check the session dir
(`%LOCALAPPDATA%\context-harness\sessions\<id>\`).

Conventions: **P#** = prompt to send. **Probe** = the bad behavior.
**Pass** = what you should observe.

---

## P1 — context.md read (gate)

**Send:** `Summarize the full content of context.md for me.`

**Probe:** Model re-reading its already-injected context (the photo bug).

**Pass:**
- The `read` call returns DENIED; the content never enters the transcript.
- The agent answers from its injected context (or says it can't read
  the file) — it does NOT retry the read or work around via `exec`.

**Verify:** `grep -c "## tool" scratch.md` after close should show no
`read` of context.md succeeding; `context.md` read denial appears once
in the archived episode.

## P2 — empty ping (grounding)

**Send:** `test`

**Probe:** Tool-call thrashing with no task (the photo bug).

**Pass:** Direct text reply, zero tool calls. No `dir`, no `find`, no
exploration.

**Verify:** `scratch.md` archives with `tool_calls: 0`; history shows
`## user t0001` → `## episode t0001` → `## assistant t0001`.

## P3 — OS-appropriate listing (environment grounding)

**Send:** `What Python files are in the current project?`

**Probe:** Unix-isms on Windows (`find . -type f | head`).

**Pass:** Uses `dir` / PowerShell / `Get-ChildItem`. No `find`, no
`head`, no `ls`.

## P4 — duplicate read (dedupe)

**Send:** `Read README.md, then read it again and confirm the first line.`

**Probe:** Repeated identical tool outputs bloating scratch.

**Pass:** The agent either answers the second half from the first
read (no second call), or the duplicate output is collapsed by
dedupe. Scratch never holds two full copies.

**Verify:** archived episode contains one `## tool` body for the file,
not two.

## P5 — history dump (edit-only + windowing)

**Send:** `Write a comprehensive 500-line summary of everything we've discussed into history.md`

**Probe:** Whole-file `write` clobbering history; unbounded growth.

**Pass:**
- `write` on history.md is DENIED (edit-only); the agent uses `edit`
  with exact old/new text.
- If the edit bloats history past the cap, H2 windows the oldest
  turns: `## history-archive` pointer appears, newest turn kept.

**Verify:** `history.md` never contains a 500-line single section;
`archive/history-*.md` exists if windowing fired.

## P6 — sat discipline (curation)

**Send:** `Remember these three things: the deploy is Friday, the API key expired, and we switched to Postgres.`

**Probe:** Sats turning into prose dumps instead of one-liners.

**Pass:** Targeted `edit` calls — one line per fact, in the right sat
(decisions for the Postgres switch with its reason if given). No
paragraphs, no duplicates of existing lines.

**Verify:** `sats/*.md` diffs are single added lines; `.bak` files
minted per edit.

## P7 — cross-turn memory (H1 auto-record)

**Send (turn 1):** `My favorite color is blue.`
**Send (turn 2):** `What did I tell you my favorite color was?`

**Probe:** Closing replies vanishing from the record (pre-H1 the model
had to manually preserve them).

**Pass:** Turn 2 answers "blue" with no archive lookback — the reply
was recorded to history deterministically.

**Verify:** `history.md` contains `## assistant t0001` with the
acknowledgment.

## P8 — windowing under load (H2)

**Send:** 10+ turns of ordinary chatter (any content), then run
`python -m harness --dry-run --session <id>`.

**Probe:** Unbounded history growth.

**Pass:** `history.md` holds recent turns plus `## history-archive`
pointers; `archive/history-*.md` files hold the moved turns verbatim;
the newest turn is always present. Dry-run verdict stays `fits`.

## P9 — tool failure (no retry loop)

**Send:** `Run nonexistent-command-xyz and tell me what happened.`

**Probe:** Retry-spiral on failing tools.

**Pass:** One attempt, ERROR result surfaced to the user, no repeat
calls with slight variations.

---

## Scorecard

| # | Behavior under test | Mechanism |
|---|---|---|
| P1 | No context.md re-read | read gate (DENIED) |
| P2 | No thrash on empty task | prompt grounding |
| P3 | OS-correct commands | {os_name}/{workdir} in prompt |
| P4 | No duplicate outputs | dedupe_exact |
| P5 | No history clobber/bloat | edit-only gate + H2 windowing |
| P6 | Sats stay one-liners | curation prompt + exactly-once |
| P7 | Replies persist | H1 auto-record |
| P8 | History bounded | H2 windowing |
| P9 | No retry loops | (model judgment; observe) |

P9 is observational — there is no deterministic mechanism for it; if
a model retry-spirals, that's a prompt-tuning signal, not a harness
bug.
