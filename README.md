# context-harness

Daily-driver agent loop with deterministic, blank-slate context assembly.

There is no accumulating transcript. Every turn the harness assembles
the model's context from sources, in fixed order:

1. **satellites** — `sats/facts.md`, `sats/decisions.md`, `sats/tasks.md`
   (durable one-liners, sent as `## sat <name>` sections)
2. **history** — `history.md` (stamped conversation record plus episode
   pointers)
3. **scratch** — `scratch.md` (the current episode's working notes:
   assistant replies and tool results)

The prompt loads fresh from `prompt.md` through the API system parameter
each turn. The assembled text is written to `context.md` as an
inspectable build artifact — rebuilt every turn. The model neither
reads nor edits it: the assembled text is already its injected
messages. No hidden transcript, no special compact tool.

## Who owns what

**The harness owns:** prompt loading, assembly order, turn/section
counters (`state.json`), the episode lifecycle, archive pointers,
source-gate enforcement, pre-edit backups, and verification. Context
management is deterministic harness behavior, not model judgment.

**The model may edit** (exact `old_text`/`new_text`, must match exactly
once):
- `history.md` — the conversation record (`## user t<NNNN>`,
  `## episode t<NNNN>`)
- `sats/facts.md`, `sats/decisions.md`, `sats/tasks.md` — one-line
  facts, decisions with reasons, open tasks

`write` is rejected on all session sources — use `edit`.

**Harness-owned (model read-only):** `prompt.md`, `state.json`,
`index.md`, `scratch.md`, `archive/`, `context.md`. `state.json`
can't even be read. Denied calls return a `DENIED` tool result the
model must respect.

## Episodes

Tool calls and their results accumulate in `scratch.md` during a turn.
When the assistant replies with no more tool calls, the episode closes:
the harness archives scratch to `archive/<date>-t<NNNN>.md`, clears it,
and appends a `## episode t<NNNN>` pointer to history (archive path,
turn range, tool-call count). Archived detail stays out of hot context
but can be re-read deliberately by path — visible cost, no silent bloat.
Move anything durable into `history.md` or the sats *before* the
closing reply, or it leaves active context.

## Budget

A token budget meter shows every turn. Soft breach warns; hard breach
gives the model a curation turn. Deterministic stages run first
(exact-dupe collapse, oldest-tool eviction, per-section caps — all on
`scratch.md`, milliseconds, no model calls); if still over, the model
gets edit-only curation turns on `history.md`/`sats/*.md` until the
next assembled request fits.
Enforcement sits at 50% of the model's context window so pruning starts
early and the prune request itself always fits; soft warns at 80% of
hard. The header shows both numbers, e.g. `window=135,168 [live]
prune-at=67,584` — the window is the context, the prune trigger is not.
Curation fails repeatedly → loud error, never silent truncation. Every
source edit backs up the file first
(`history.pre-edit-<ts>.bak`), so all model curation is reversible. The
curation turn can run on a separate janitor model — cheaper/smaller, even
local while the main model is cloud (see `prune_*` config).

Every model edit of a source prints a mechanical diff: which sections
were removed/added and how many tokens were recovered. You always
see what the model threw away.

Design follows the Context Language Models paper (arXiv:2609.37725),
implemented from scratch for interactive use.

## Session layout

```
<sessions>/<id>/
  prompt.md        system prompt (harness-owned; loaded fresh each turn)
  state.json       turn/section counters (harness-owned, unreadable)
  index.md         pointer map (harness-owned)
  history.md       stamped conversation record (model-editable)
  sats/            facts.md, decisions.md, tasks.md (model-editable)
  scratch.md       current episode working notes (harness-owned)
  archive/         closed episodes, one file per turn (harness-owned)
  context.md       assembled artifact for inspection (rebuilt every turn)
```

Old single-file sessions migrate automatically on open: user sections
become stamped history, each turn's assistant/tool run becomes an
archived episode, the turn counter is seeded, and the original is kept
as `context.md.pre-migration-<ts>.bak`. Ambiguous transcripts refuse
to migrate loudly instead of guessing.

## Run

```bash
pip install -r requirements.txt
python -m harness --config        # writes platform-default config.json
python -m harness "summarize this repo"
python -m harness                 # REPL, continues current session
python -m harness --new "task"    # fresh session
python -m harness --list          # list sessions
python -m harness --tui          # terminal UI (needs the optional extra)
```

The REPL stays in its session: plain text appends to the same
`context.md`. Slash commands (also in `/help`):

- `/list` — show sessions (`*` = current)
- `/new` — fresh session and switch to it
- `/new summarize this repo` — fresh session + run that task immediately
- `/open 20261005-0826` — switch back (full id or unambiguous prefix)
- `/project [name]` — show/switch project
- `/usage [N]` — ledger totals + last N calls (default 5)
- `/config` — show effective config (redacted)
- `/providers` — list known providers
- `/models` — list models from current provider (OpenAI-compatible)
- `/quit` — leave (empty line also quits)

Flow: `/new` once, then just type. Only `/new` mints a new
`sessions/<id>/context.md`; everything else appends to the current one.

## TUI (optional)

The CLI is the default interface and works with stdlib-only deps.
The TUI is the same agent loop with a terminal UI — install the extra:

```bash
pip install -r requirements-tui.txt   # textual
python -m harness --tui
```

Same session, same provider registry, same ledger as the CLI.

- transcript pane (assistant text, tool calls, budget/prune lines),
  budget bar, status line, input box
- chat-style message panels: blue for you, dark for assistant, black for
  tools — click any message to copy its full text to the clipboard
- mutating tools pop an approval dialog: `a` turn / `s` session /
  `d` or `esc` deny, countdown default-deny (same timeout as CLI)
- `ctrl+s` session picker, `ctrl+o` provider/model picker,
  `ctrl+d` debug-log tail, `ctrl+e` export transcript to a file
- `ctrl+c` copies the current selection (never quits — quit is `/quit`
  or `ctrl+q`)
- slash commands work in the input box exactly like the REPL
  (`/new`, `/open`, `/list`, `/project`, `/usage`, `/config`,
  `/providers`, `/models`, `/help`, `/quit`)
- `--no-mouse` leaves selection to the terminal instead (keyboard
  still drives everything in-app)
- turn errors tee to `<session-dir>/tui-errors.log` with tracebacks

## Requirements

- Python 3.10+
- `tiktoken` (pinned in `requirements.txt`) — the budget meter counts
  with cl100k_base on every machine, so enforcement is consistent. First
  run downloads the BPE file once (needs internet; set `TIKTOKEN_CACHE_DIR`
  to control where it lands).

## Config

Platform-default location (`%APPDATA%/context-harness/config.json` on
Windows, `~/.config/context-harness/config.json` elsewhere):
`provider` (`openai` / `anthropic` / `nvidia`), `model`, `base_url` (any
OpenAI-compatible `/v1`, including local servers), `budget_hard` /
`budget_soft`, `approval_timeout`, `exec_timeout`, `exec_timeout_max`,
`request_timeout`, `usage_note`, `prune_target`, `prune_keep_tools`,
`prune_section_cap`. API key comes from the environment (`api_key_env`) —
never from the file. `sessions_dir` unset selects the platform default
(`%LOCALAPPDATA%` on Windows).

Budgets auto-size and report their source. Omit `budget_hard` /
`budget_soft` for auto: a live server value (`[live]` — llama.cpp,
vLLM, LM Studio, Ollama, Anthropic), a published-registry fallback
(`[registry]`, only where the API exposes no window), or the built-in
default (`[default]`). Set explicit numbers to pin values (`[explicit]`)
and skip detection. Enforcement runs at 50% of the window; the session
header shows both, e.g. `window=135,168 [live] prune-at=67,584`.

CLI `--provider` accepts a registry name or a kind
(`openai`/`anthropic`/`nvidia`); registry first, legacy kind fallback.
Kind overrides also reset `base_url` to the provider default
unless you set `base_url` in `config.json`. This prevents a leftover llama.cpp
URL from being used with NVIDIA/OpenAI.

Named providers (optional registry — existing configs work untouched):

```json
{ "provider": "mylocal",
  "providers": {
    "mylocal": { "kind": "openai",
                 "base_url": "http://127.0.0.1:8080/v1",
                 "model": "Qwen", "api_key_env": null }
  } }
```

`provider` names a registry entry when it matches, else a kind.
Entry fields fall back per-field to that kind's defaults;
`api_key_env: null` means local, no key. Reachability (`/providers`
dot, picker dot) is display-only and never blocks a connection.
Add-flow templates: `openai-cloud`, `anthropic-cloud`, `nvidia-cloud`,
`llama.cpp`, `lmstudio`, `ollama` (see `PROVIDER_TEMPLATES` in
`harness/config.py`). `/providers save-current <name>` snapshots the
live connection into the registry.

Keys can live in a `.env` file instead of exports (see `.env.example`):
`<config-dir>/.env` loads first, then `./.env`; real environment
variables always win. `--env-file PATH` points at one explicitly.
`.env` is git-ignored; only `.env.example` commits.

Local server example (llama.cpp + Qwen — OpenAI-compatible `/v1`):

```json
{ "provider": "openai", "model": "Qwen",
  "base_url": "http://127.0.0.1:8080/v1" }
```

llama.cpp server defaults to `http://127.0.0.1:8080/v1`;
LM Studio defaults to `http://localhost:1234/v1`. The model id is
whatever the server advertises under `/v1/models` (here `Qwen`).

Cloud (key from env or `.env`, e.g. `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`):

```json
{ "provider": "openai", "model": "gpt-5" }
```

NVIDIA example (API key via `NVIDIA_API_KEY` env / `.env`):

```json
{ "provider": "nvidia", "model": "nemotron-4-340b-instruct" }
```

Janitor model for prune-only turns (optional — cheaper/smaller model,
can be local while main is cloud):

```json
{ "prune_provider": "openai", "prune_model": "small-local-model",
  "prune_base_url": "http://localhost:1234/v1" }
```

Each `prune_*` falls back to the main setting when unset.

No API key needed when `base_url` is overridden — the Authorization
header is simply omitted. (Tool calling with local models depends on the
model's chat template; results vary.)

## Layout

`harness/loop.py` — agent loop, budget enforcement, curation turns ·
`harness/context.py` — section render/parse, token counting ·
`harness/session.py` — session layout, counters, migration ·
`harness/assembly.py` — blank-slate assembly (sats → history → scratch) ·
`harness/providers.py` — OpenAI, Anthropic, Mock ·
`harness/tools.py` — exec / read / write / edit / tokens ·
`harness/config.py`, `harness/__main__.py` — config, CLI ·
`harness/debug.py` — JSONL debug log ·
`harness/approvals.py` — approval policy (allow/ask/deny) ·
`harness/usage.py` — server usage ledger (`usage.json`) ·
`harness/project.py` — project registry + session markers ·
`harness/spinner.py` — console activity indicator ·
`harness/deterministic.py` — model-free prune stages ·
`harness/tui/` — optional Textual interface (`--tui`): app, bridge,
widgets, commands, modal approvals (needs `requirements-tui.txt`)

`tests/` — 225 unittest tests; the Textual pilots skip cleanly when
the extra is missing. `python -m unittest discover -s tests`

## Deterministic stages

Before the janitor turn fires, model-free stages run on `scratch.md`
(milliseconds, no prefill): exact-dupe collapse (newest wins),
oldest-tool eviction (newest K kept), per-section caps (head+tail).
Evicted tool ids are scrubbed from `tool-calls` fences so strict
servers never see dangling calls. The agent curation turn is the last
resort, over a much smaller file. Knobs: `prune_target` (default soft),
`prune_keep_tools` (5), `prune_section_cap` (8000).

## Projects

A project is a name plus a workdir (`<config-dir>/projects.json`):

```bash
python -m harness --project demo "task"   # resume demo or start it (cwd)
python -m harness --project demo --workdir D:\work\demo "task"
```

Sessions are stamped with their project; `/project` shows the current
one, `/project demo` resumes demo's latest session (or starts one).
Until modes land, a project is one ephemeral `[project: NAME]` line per
request (sent, never stored) — the hook SPEC/facts/goal will hang off.

## Usage accounting

Every model call prints its window share with the parts that make it up:

```
[context 929 (sys 492 + chat 19 + tools 418) / 100,000 (1%) | sess in 0 out 0]
```

`sys` = harness instructions, `chat` = wire-format transcript,
`tools` = schemas riding along. Server-reported `input`/`output`
accumulate in `<session-dir>/usage.json` (per-turn breakdowns plus
lifetime totals, shown as `sess in/out` and in the session header), so
the CLI and the TUI read the same ledger. `request` /
`response` events already carry `breakdown` + `usage_total` for that.
`/usage [N]` prints the ledger; the `tokens` tool lets the model count
any file or text with the same estimator instead of guessing. Every
request also ends with an ephemeral `[harness note: ...]` usage line
(sent, never stored; off via `usage_note: false` or `--no-usage-note`)
so the model reasons from live numbers, not stale pasted ones.

## Approvals

Reads inside the project dir and the session dir run free, as do
read-only probes (`ls`, `git status`, ...). Mutating calls ask first:

```bash
python -m harness "task"          # prompts on mutating tools
python -m harness --yes "task"    # auto-approve (denylist still denied)
```

Each prompt offers `(a)pprove turn / (s)ession / (d)eny` with a 120s
default-deny timeout — the slot the future notification system will
hook (`approval-wait` / `approval-result` events already fire for it).
The TUI shows the same decision as an in-app modal with a countdown,
same keys and same default-deny.
Session approvals persist in `<session-dir>/approvals.json`, so "approved
for this session" survives across turns and restarts; turn approvals
clear every `run_turn`. The model's source curation (`history.md`,
`sats/*.md`) never prompts. Denials return a `DENIED` tool result the
model must respect.

Never allowed (no prompt): destructive shell (`rm -rf /`, `mkfs`,
`dd of=/dev`, fork bombs, power commands, `curl|sh`) and writes under
`~/.ssh`, `~/.gnupg`, `~/.aws`.

Tunable (config file, CLI flag wins):

| setting | default | flag |
|---|---|---|
| `approval_timeout` | 120s | `--approval-timeout` |
| `exec_timeout` | 60s | `--exec-timeout` |
| `exec_timeout_max` | 300s | `--exec-timeout-max` |
| `request_timeout` | 120s | `--request-timeout` (raise for ~100k prompts on slow local servers — prefill can take many minutes) |
| `prune_request_timeout` | 3× `request_timeout` | (config only; prune prompts carry the whole file, so the janitor gets longer — raise both for huge files on slow servers) |

Experiment flags (no config edit needed): `--budget-hard`, `--budget-soft`, `--request-timeout`, `--yes`, `--project`, `--debug`, `--tui`, `--no-mouse`.

## Debugging

```bash
python -m harness --debug "task"              # JSONL log next to the transcript
python -m harness --debug-file .debug/h.jsonl "task"  # explicit path
```

`--debug` writes `<session-dir>/debug.jsonl` (next to `context.md`);
`--debug-file PATH` implies `--debug` and writes there instead.
One JSON object per line: `{"ts", "session", "kind", "data"}` covering
`session`, `request` (full messages + token estimate), `response`
(content, tool calls, usage), `tool` (name, args, result), `budget`,
`prune`, `context-diff`, `usage`, and `error`. Console output is
unchanged — the file is the tee. `.debug/` and `debug.jsonl` are
git-ignored.

## Notes

- `exec` runs with your user privileges. Your machine, your responsibility.
- One session in the foreground at a time.
- A `## tool <id>` section with no matching tool call is dropped on parse,
  never sent to the provider.
- Assistant replies are sanitized before display/storage: echoed `## `
  headers and ```tool-calls fences (local models mimic the file format)
  are stripped so phantom sections can't accumulate.
- The meter is a cl100k_base estimate of system + messages + tool schemas
  — consistent for enforcement, but your model's own tokenizer counts
  differently (compare `usage` in `--debug`). Server network stalls
  surface as `PROVIDER ERROR`, never a traceback.
