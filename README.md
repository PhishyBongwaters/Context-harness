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
Pruning fails repeatedly → loud error, never silent truncation. The prune
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

## Requirements

- Python 3.10+
- `tiktoken` (pinned in `requirements.txt`) — the budget meter counts
  with cl100k_base on every machine, so enforcement is consistent. First
  run downloads the BPE file once (needs internet; set `TIKTOKEN_CACHE_DIR`
  to control where it lands).

## Config

Platform-default location (`%APPDATA%/context-harness/config.json` on
Windows, `~/.config/context-harness/config.json` elsewhere):
`provider` (`openai` / `anthropic`), `model`, `base_url` (any
OpenAI-compatible `/v1`, including local servers), `budget_hard` /
`budget_soft`. API key comes from the environment (`api_key_env`) —
never from the file. `sessions_dir` unset selects the platform default
(`%LOCALAPPDATA%` on Windows).

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
`harness/tools.py` — exec / read / write / edit ·
`harness/config.py`, `harness/__main__.py` — config, CLI ·
`harness/debug.py` — JSONL debug log

`tests/` — 49 unittest tests, stdlib only. `python -m unittest discover -s tests`

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
