# context-harness

A daily-driver agent loop where the model treats its context as a file.

## The idea

Normal agents keep a hidden transcript and the framework silently truncates
it when it gets long. Here the transcript **is** a file: `context.md`
holds the whole conversation as `## user` / `## assistant` /
`## tool <id>` sections (assistant tool calls ride in a fenced
`tool-calls` JSON block). Before every model call the harness reads the
file and sends it as the conversation; after each turn it appends the new
reply and tool results to the end. The model restructures anything above
with its ordinary write/edit tools — delete stale sections, summarize old
output in place, reorder. No special compact tool; the file is the memory.

The harness shows a token budget meter every turn and enforces it:

- **soft breach** → warning, turn continues
- **hard breach** → the normal turn is *not* sent. The model gets a
  prune-only turn (write/edit on `context.md` only, ephemeral transcript)
  until usage is back under the limit. If pruning fails after several
  attempts, the harness raises loudly instead of silently truncating.

The model is its own garbage collector. (The design follows the
"Context Language Models" paper, arXiv:2609.37725 — implemented here from
scratch for interactive use, not forked.)

## Quickstart

```bash
cd context-harness
python -m harness --config        # writes ~/.config/context-harness/config.json
export OPENAI_API_KEY=...         # or ANTHROPIC_API_KEY
python -m harness "summarize the repo layout"
python -m harness                 # REPL, continues the current session
python -m harness --new "task"    # fresh session
python -m harness --list          # list sessions
```

No dependencies beyond the Python 3 standard library. If `tiktoken` is
installed it is used for the budget meter, otherwise a chars/4 heuristic
(the meter always says which estimator is active).

## Providers

`openai` → `/v1/chat/completions`, `anthropic` → `/v1/messages`.
`base_url` in the config can point at any OpenAI-compatible `/v1`
endpoint — including a local server. API keys come from the environment
(`api_key_env`), never from the config file.

## Layout

```
harness/
  __main__.py    CLI: one-shot, REPL, sessions
  loop.py        agent loop, budget enforcement, prune-only turns
  context.py     context.md management, token counting, budget meter
  tools.py       exec / read / write / edit
  providers.py   OpenAI, Anthropic, Mock (tests)
  config.py      ~/.config/context-harness/config.json + env
tests/           unittest suite, stdlib only
```

## V1 limits (honest)

- No streaming output; tool results print when they return.
- No approval gate on `exec` — it runs with your user privileges.
  This is a daily driver for your own machine, not a sandbox.
- One session at a time in the foreground.
- The prune turn is a pragmatic escape hatch: it re-sends the over-budget
  file one more time with editing tools only.
