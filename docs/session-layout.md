# Session layout example

A real session directory after ~10 turns. This is what the harness
manages — the model never writes these files directly (except via
`edit`/`write` tools to `sats/` when instructed).

```
sessions/2026-10-10-abc123/
├── prompt.md            # System prompt (harness template + user includes)
├── state.json           # Session metadata: id, model, created, budget
├── index.md             # HOLY — model-curated, machine-verified anchors
├── context.md           # Assembled transcript (harness build artifact)
├── history.md           # Curated durable transcript (episodes)
├── scratch.md           # Working notes, pruned deterministically
├── result.md            # Final answer (written at turn end)
├── sats/
│   ├── current.md       # What the agent is doing right now
│   ├── goals.md         # User's stated goals for this session
│   ├── facts.md         # Confirmed facts (promoted from hypotheses)
│   ├── decisions.md     # Decisions made + rationale
│   ├── tasks.md         # Task list with status
│   └── hypotheses.md    # Half-formed ideas, unproven suspicions
└── archive/
    └── context.archive-20261010-093000.md  # Pruned-away old sections
```

## File roles

| File | Written by | Purpose |
|------|------------|---------|
| `prompt.md` | Harness (`init_layout`) | System prompt template. Never clobbered if it exists. |
| `state.json` | Harness | Session id, provider/model, timestamps, budget. |
| `index.md` | Model (curated), harness (verified) | Stable anchors into history/sats. "Holy and must be pure." |
| `context.md` | Harness (`assemble`) | Full transcript: system + history + sats + scratch. What the model sees. |
| `history.md` | Harness (janitor) | Episodes: condensed turn summaries. Survives pruning. |
| `scratch.md` | Model (via tools) | Working space. Pruned by the deterministic ladder. |
| `result.md` | Harness | The final answer, extracted at turn end. |
| `sats/*.md` | Model (via tools) | Satellite files: curated knowledge layers. |
| `archive/` | Harness | Old sections moved here when >2x over target. |

## What pruning does

1. **Dedupe** — exact-duplicate sections removed.
2. **Tool eviction** — oldest tool-result sections evicted (keeps newest N).
3. **Section cap** — oversized sections truncated.
4. **Archive** — if still >2x over target, oldest half moved to `archive/`.
5. **Model curation** (if still over) — janitor model condenses to `history.md`.

After pruning, `context.md` is reassembled from the surviving pieces.
The model sees a smaller context; nothing is lost (it's in `history.md`
or `archive/`).

## Include file

A user-provided text file appended to the system prompt under
`# User includes`. Set via:

- `--include path/to/file` — one-shot, this session only.
- `include_file` in config — persistent, every session.
- `--include-subagents` — also inject into subagent prompts.

Example `~/.config/harness/include.md`:

```markdown
Always use Canadian spelling.
My project uses tabs, not spaces.
When in doubt, ask before deleting files.
```
