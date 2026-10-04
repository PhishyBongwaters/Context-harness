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
Pruning fails repeatedly → loud error, never silent truncation.

Design follows the Context Language Models paper (arXiv:2609.37725),
implemented from scratch for interactive use.

## Run

```bash
python -m harness --config        # writes ~/.config/context-harness/config.json
export OPENAI_API_KEY=...         # or ANTHROPIC_API_KEY
python -m harness "summarize this repo"
python -m harness                 # REPL, continues current session
python -m harness --new "task"    # fresh session
python -m harness --list          # list sessions
```

Stdlib only. `tiktoken` used for the meter if installed, else chars/4
(the meter says which).

## Config

Platform-default location (`%APPDATA%/context-harness/config.json` on
Windows, `~/.config/context-harness/config.json` elsewhere):
`provider` (`openai` / `anthropic`), `model`, `base_url` (any
OpenAI-compatible `/v1`, including local servers), `budget_hard` /
`budget_soft`. API key comes from the environment (`api_key_env`) —
never from the file. `sessions_dir` unset selects the platform default
(`%LOCALAPPDATA%` on Windows).

## Layout

`harness/loop.py` — agent loop, budget enforcement, prune turns ·
`harness/context.py` — transcript render/parse, token counting ·
`harness/providers.py` — OpenAI, Anthropic, Mock ·
`harness/tools.py` — exec / read / write / edit ·
`harness/config.py`, `harness/__main__.py` — config, CLI

`tests/` — 31 unittest tests, stdlib only. `python -m unittest discover -s tests`

## Notes

- `exec` runs with your user privileges. Your machine, your responsibility.
- One session in the foreground at a time.
- A `## tool <id>` section with no matching tool call is dropped on parse,
  never sent to the provider.
