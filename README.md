# context-harness

Daily-driver agent loop where the model treats its context as a file.

`context.md` **is** the transcript — `## user` / `## assistant` /
`## tool <id>` sections, tool calls in a fenced `tool-calls` JSON block.
The harness reads it before every model call and appends new turns at the
end. The model restructures the rest with plain write/edit tools: delete
stale sections, summarize old output, reorder. No hidden transcript, no
special compact tool.

A token budget meter shows every turn. Soft breach warns; hard breach
gives the model a prune-only turn (write/edit on `context.md` only).
Pruning fails repeatedly → loud error, never silent truncation. Every
prune turn backs up `context.md` first (`context.pre-prune-<ts>.bak`),
so all model curation is reversible. The prune
turn can run on a separate janitor model — cheaper/smaller, even local
while the main model is cloud (see `prune_*` config).

Every model edit of the transcript prints a mechanical diff: which
sections were removed/added and how many tokens were recovered. You always
see what the model threw away.

Design follows the Context Language Models paper (arXiv:2609.37725),
implemented from scratch for interactive use.

## Run

```bash
pip install -r requirements.txt
python -m harness --config        # writes platform-default config.json
python -m harness "summarize this repo"
python -m harness                 # REPL, continues current session
python -m harness --new "task"    # fresh session
python -m harness --list          # list sessions
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

`harness/loop.py` — agent loop, budget enforcement, prune turns ·
`harness/context.py` — transcript render/parse, token counting ·
`harness/providers.py` — OpenAI, Anthropic, Mock ·
`harness/tools.py` — exec / read / write / edit / tokens ·
`harness/config.py`, `harness/__main__.py` — config, CLI ·
`harness/debug.py` — JSONL debug log ·
`harness/approvals.py` — approval policy (allow/ask/deny) ·
`harness/usage.py` — server usage ledger (`usage.json`) ·
`harness/project.py` — project registry + session markers ·
`harness/spinner.py` — console activity indicator ·
`harness/deterministic.py` — model-free prune stages

`tests/` — 109 unittest tests, stdlib only. `python -m unittest discover -s tests`

## Deterministic prune

Before the janitor turn fires, model-free stages run (milliseconds, no
prefill): exact-dupe collapse (newest wins, user sections exempt),
oldest-tool eviction (newest K kept), per-section caps (head+tail).
Evicted tool ids are scrubbed from `tool-calls` fences so strict
servers never see dangling calls. The agent turn is the last resort,
over a much smaller file. Knobs: `prune_target` (default soft),
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
the CLI — and later a TUI/GUI — read the same ledger. `request` /
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
Session approvals persist in `<session-dir>/approvals.json`, so "approved
for this session" survives across turns and restarts; turn approvals
clear every `run_turn`. The model's own `context.md` curation never
prompts. Denials return a `DENIED` tool result the model must respect.

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

Experiment flags (no config edit needed): `--budget-hard`, `--budget-soft`, `--request-timeout`, `--yes`, `--project`, `--debug`.

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
