# Subagent Execution — specification

**Status:** draft (2026-10-09)
**Branch:** (none yet)

## 1. What this is and why

The harness runs a single agent loop: user → harness → model → tools →
harness → model … until the episode closes. **Subagent execution** adds
the ability for the main agent to spawn **child agents** (subagents) that
run independently, each with their own context, tools, and budget, while
the parent loop continues or waits.

Why: some tasks are naturally parallelizable (review multiple files, run
independent tests, gather information from different repos). A subagent
can work on its slice without bloating the parent's context, and results
flow back as structured outputs rather than tool-call noise.

Non-goals: multi-agent orchestration frameworks, distributed execution,
model-to-model handoff protocols, or changing the single-agent loop for
tasks that don't need subagents.

## 2. Core model

```
┌─────────────────────── parent loop ───────────────────────┐
│                                                           │
│  user → harness → model → tools → … → model              │
│                          │                                │
│                          ├─ spawn_subagent(task, config)   │
│                          │   → subagent_id                 │
│                          │                                │
│                          ├─ wait_subagent(id, timeout?)    │
│                          │   → {status, output, tokens}    │
│                          │                                │
│                          └─ list_subagents()               │
│                              → [{id, status, task}]        │
│                                                           │
└───────────────────────────────────────────────────────────┘
         │
         ▼  (independent, parallel)
┌─────────────────────── subagent ─────────────────────────┐
│  isolated session dir                                     │
│  own context assembly (sats/history/scratch)              │
│  own budget, own provider (or inherited)                  │
│  own tool set (inherited or restricted)                   │
│  writes results to a known path                           │
└───────────────────────────────────────────────────────────┘
```

A subagent is a **full harness loop** running in its own session directory.
It is not a tool call — it is a parallel harness instance. The parent
harness manages its lifecycle via three new tools.

## 3. Session layout for subagents

Each subagent gets a temporary session directory:

```
<sessions>/subagents/<subagent_id>/
  prompt.md            # inherited or derived from parent
  state.json           # harness-owned
  index.md             # harness-owned
  history.md           # model-editable (subagent's own history)
  sats/
    current.md         # the subagent's task
    goals.md           # derived from parent's task
    facts.md           # subagent's own discoveries
    decisions.md       # subagent's own decisions
    tasks.md           # subagent's own tasks
  scratch.md           # harness-owned
  archive/             # harness-owned
  context.md           # harness-owned build artifact
  result.json          # harness-owned; written on completion
```

The `subagents/` directory lives under the parent's session directory.
It is **not** a regular session — it is managed entirely by the parent
harness. The subagent's session ID is `<subagent_id>` (a UUID or
monotonic counter), and it is never the `.current` session.

## 4. Tool definitions

Three new tools, added to `tool_definitions()` in `harness/tools.py`:

### 4.1 `spawn_subagent`

```json
{
  "name": "spawn_subagent",
  "description": "Spawn a child agent to work on a subtask in parallel.",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "The subtask description. Be specific — this becomes the subagent's entire context."
      },
      "config": {
        "type": "object",
        "description": "Optional configuration overrides.",
        "properties": {
          "model": {
            "type": "string",
            "description": "Model ID (default: inherit from parent)."
          },
          "provider": {
            "type": "string",
            "description": "Provider name (default: inherit from parent)."
          },
          "budget_hard": {
            "type": "integer",
            "description": "Hard budget in tokens (default: 1/4 of parent's hard budget)."
          },
          "budget_soft": {
            "type": "integer",
            "description": "Soft budget in tokens (default: 80% of subagent's hard budget)."
          },
          "max_steps": {
            "type": "integer",
            "description": "Maximum tool-call steps (default: 20)."
          },
          "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tool names to allow (default: all parent tools). Use [] for read-only, or specify a subset like [\"read\", \"edit\", \"tokens\"]."
          },
          "timeout": {
            "type": "integer",
            "description": "Maximum wall-clock seconds (default: 300)."
          },
          "workdir": {
            "type": "string",
            "description": "Working directory for the subagent (default: parent's workdir)."
          }
        }
      }
    },
    "required": ["task"]
  }
}
```

Returns: `{"subagent_id": "<id>", "status": "running"}`

### 4.2 `wait_subagent`

```json
{
  "name": "wait_subagent",
  "description": "Wait for a subagent to complete and return its result.",
  "parameters": {
    "type": "object",
    "properties": {
      "subagent_id": {
        "type": "string",
        "description": "The subagent ID returned by spawn_subagent."
      },
      "timeout": {
        "type": "integer",
        "description": "Seconds to wait (default: 60). Use 0 for non-blocking check."
      }
    },
    "required": ["subagent_id"]
  }
}
```

Returns (on completion):
```json
{
  "subagent_id": "<id>",
  "status": "completed",
  "output": "the subagent's final reply text",
  "tokens_used": 12345,
  "steps": 7
}
```

Returns (on timeout with `timeout > 0`):
```json
{
  "subagent_id": "<id>",
  "status": "running",
  "message": "still running after 60s"
}
```

Returns (non-blocking check with `timeout = 0`):
```json
{
  "subagent_id": "<id>",
  "status": "running|completed|failed|cancelled"
}
```

### 4.3 `list_subagents`

```json
{
  "name": "list_subagents",
  "description": "List all subagents spawned by this parent, with their status.",
  "parameters": {
    "type": "object",
    "properties": {}
  }
}
```

Returns:
```json
[
  {"subagent_id": "abc123", "task": "review auth module", "status": "completed"},
  {"subagent_id": "def456", "task": "run integration tests", "status": "running"}
]
```

## 5. Subagent lifecycle

### 5.1 Spawn

1. Parent calls `spawn_subagent(task, config)`.
2. Harness creates a new session directory under `<parent_dir>/subagents/<id>/`.
3. Harness initializes the blank-slate layout (T1): prompt, state, index,
   history, sats, scratch, archive.
4. The subagent's `sats/current.md` is set to the task description.
5. The subagent's prompt is derived from the parent's `SYSTEM_PROMPT` with
   a modification: it knows it is a subagent and that its output will be
   read by a parent agent (not a human).
6. A background thread starts the subagent loop (`Loop.run_turn` equivalent)
   with the subagent's own budget, tools, and provider.
7. Returns the subagent ID immediately (non-blocking).

### 5.2 Execution

The subagent runs exactly like a normal harness turn:

1. Assembles context from its own sats/history/scratch.
2. Sends to its model (inherited or overridden).
3. Executes tool calls (filtered by the `tools` config).
4. Archives episodes, windows history, enforces budget.
5. On completion (no more tool calls), records the final reply to its
   `result.json` and sets status to `completed`.

The subagent's loop runs in a **separate thread** from the parent. The
parent's turn is not blocked by the spawn — it continues immediately.

### 5.3 Completion

A subagent completes when:

- Its reply has no tool calls (normal completion).
- It exceeds `max_steps`.
- It exceeds its budget and pruning fails.
- Its wall-clock `timeout` expires.
- The parent cancels it (see §5.5).

On completion, the harness writes `result.json`:

```json
{
  "subagent_id": "<id>",
  "status": "completed",
  "output": "final reply text",
  "tokens_used": 12345,
  "steps": 7,
  "completed_at": "2026-10-09T14:30:00"
}
```

### 5.4 Failure

If a subagent fails (budget exceeded, provider error, etc.), `result.json`
records `status: "failed"` with an error message. The parent can inspect
this via `wait_subagent` or by reading the result file directly.

### 5.5 Cancellation

The parent can cancel a running subagent by calling a new internal method
`Loop.cancel_subagent(id)`. This sets a stop event on the subagent's
thread (same mechanism as `Loop.request_stop()`). The subagent finishes
its current tool call (bounded by exec timeout), then exits with
`status: "cancelled"`.

## 6. Prompt modification for subagents

The subagent's prompt is the parent's `SYSTEM_PROMPT` with these
modifications:

1. **Role awareness:** The system prompt includes a note that the agent
   is a subagent working on behalf of a parent agent. Its output will be
   consumed programmatically, not read by a human.

2. **No slash commands:** Subagents cannot use `/new`, `/open`, `/list`,
   `/quit`, etc. These are stripped from the available tool set.

3. **No parent session access:** Subagents cannot read or write files
   outside their own session directory or the parent's workdir. The
   source gates (T5) are extended to deny access to `<parent_dir>/`
   except for the explicitly allowed `workdir`.

4. **Budget awareness:** The subagent's prompt includes its own budget
   limits, not the parent's.

Example subagent system prompt prefix:

```
You are a subagent running inside a context-as-file harness. You are
working on behalf of a parent agent. Your output will be read by the
parent agent, not a human. Be concise and structured.
```

## 7. Tool restrictions

By default, a subagent inherits all parent tools. The `tools` config
parameter allows restricting this:

- `[]` — read-only mode (only `read`, `tokens`).
- `["read", "edit", "tokens"]` — can read and edit files in workdir.
- `["read", "exec", "tokens"]` — can execute commands.
- `["read", "exec", "edit", "tokens"]` — full access (default).

The `spawn_subagent`, `wait_subagent`, and `list_subagents` tools are
**never** available to subagents (no recursive spawning).

Source gates are extended: a subagent's `classify_source` must also
check that the target path is within the allowed workdir. Attempts to
access the parent's session directory are denied.

## 8. Context assembly for subagents

Subagents use the same blank-slate assembly as the parent:

```
sats (current, goals, facts, decisions, tasks)
→ history.md
→ scratch.md
```

The key difference: the subagent's `sats/current.md` is initialized to
the task string, and its `sats/goals.md` is derived from the parent's
task context (e.g., "complete the subtask described in current.md").

## 9. Result passing

When the parent calls `wait_subagent(id)`, the harness:

1. Checks if the subagent has completed (reads `result.json`).
2. If completed, returns the output, token count, and step count.
3. If not completed and `timeout > 0`, blocks (polling every 1s) until
   completion or timeout.
4. If not completed and `timeout = 0`, returns the current status only.

The parent can then incorporate the subagent's output into its own
context — e.g., by editing its `sats/facts.md` with the findings, or
by continuing its own turn with the result as a user message.

## 10. Concurrency model

- Each subagent runs in its own thread.
- The parent's main thread continues its turn while subagents run.
- The parent can spawn multiple subagents in parallel.
- Subagents do **not** share state with each other or with the parent
  (except through the result mechanism).
- Each subagent has its own `UsageTracker` instance.
- The parent's `UsageTracker` does **not** include subagent token usage
  (subagents are metered separately).

## 11. Integration with existing mechanisms

### 11.1 Budget

Subagents have independent budgets. The parent's budget is unaffected
by subagent usage. If a subagent exceeds its budget, it fails
independently — the parent is not penalized.

### 11.2 Approvals

Subagents inherit the parent's `Approver` policy by default. This means
mutating tool calls in subagents still require human approval (unless
`--yes` is passed). The approval modal shows the subagent ID so the
human knows which task is being approved.

An alternative mode (`config.subagent_approvals: "auto"`) could allow
subagents to execute tools without approval — this is a config option,
not the default.

### 11.3 History windowing

Subagents have their own history windowing (H2), independent of the
parent. The parent's history is unaffected by subagent activity.

### 11.4 Archive

Subagent episodes are archived under `<parent_dir>/subagents/<id>/archive/`.
When the parent is done with a subagent, the entire `subagents/<id>/`
directory can be deleted (or kept for audit). There is no automatic
cleanup — the parent decides.

### 11.5 Prune turns

Subagents have their own prune turns, independent of the parent. A
parent prune turn does not affect running subagents.

## 12. Implementation plan

### Phase 1 — Core infrastructure
- **S1 — Subagent session layout.** Create `subagents/<id>/` directory
  structure. Extend `init_layout` to support subagent mode.
- **S2 — Subagent thread.** Background thread that runs a `Loop` instance
  with its own session dir, budget, and tools. Hook into the existing
  `Loop` class — no changes to the main loop logic.
- **S3 — Subagent registry.** A thread-safe dict mapping subagent IDs to
  their thread + session dir + result state.

### Phase 4 — Tool definitions
- **S4 — Tool definitions.** Add `spawn_subagent`, `wait_subagent`,
  `list_subagents` to `tool_definitions()`.
- **S5 — Tool executors.** Implement the executor functions in
  `harness/tools.py`. `spawn_subagent` creates the session and starts
  the thread. `wait_subagent` polls the registry. `list_subagents`
  iterates the registry.
- **S6 — Source gates.** Extend `classify_source` and `source_gate` to
  handle subagent paths. Subagents can only access their session dir
  and the configured workdir.

### Phase 3 — Prompt and config
- **S7 — Subagent prompt.** Derive the subagent system prompt from the
  parent's prompt with the role-awareness modification.
- **S8 — Config.** Add `subagent_approvals` config option (default:
  inherit from parent). Add default `budget_hard` / `budget_soft`
  fractions for subagents.

### Phase 4 — Integration
- **S9 — Approval integration.** Pass subagent ID through the approval
  flow so the human knows which subagent is requesting approval.
- **S10 — Usage tracking.** Subagents get their own `UsageTracker`.
  Parent's tracker does not include subagent usage.
- **S11 — Cancellation.** Implement `cancel_subagent` using the existing
  `TurnInterrupted` mechanism.

### Phase 5 — Verification
- **S12 — Tests.** Unit tests for spawn/wait/list, budget isolation,
  tool restrictions, cancellation.
- **S13 — Manual tests.** Spawn a subagent on a real task, verify it
  completes and returns results. Verify parent continues during
  subagent execution.

## 13. Open questions

1. **Recursive spawning:** Should subagents be able to spawn their own
   subagents? (Decision: no, for now. Recursive spawning creates
   unbounded fan-out. If needed later, add a depth limit.)

2. **Subagent-to-subagent communication:** Should subagents be able to
   communicate directly? (Decision: no. All communication flows through
   the parent. This keeps the model simple and avoids race conditions.)

3. **Result format:** Should subagents return structured output (JSON)
   or plain text? (Decision: plain text, but the prompt encourages
   structured output. The parent can parse it. Adding a strict JSON
   schema would constrain the subagent too much.)

4. **Default budget:** What fraction of the parent's budget should a
   subagent get? (Decision: 1/4 of parent's hard budget, 80% of that
   for soft. This prevents a single subagent from consuming the entire
   parent budget.)

5. **Max concurrent subagents:** Should there be a limit? (Decision:
   yes, configurable. Default: 5. Prevents resource exhaustion.)

6. **Subagent visibility in TUI:** Should the TUI show running subagents?
   (Decision: yes, in a separate pane. Phase 2+ TUI work.)

7. **Error propagation:** If a subagent fails, should the parent's turn
   fail too? (Decision: no. The parent gets a `failed` status and can
   decide how to proceed — retry, skip, or adapt.)

## 14. Design decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Subagent as full loop | Yes | Reuse existing harness logic; no special-case execution |
| Thread-based concurrency | Yes | Simple, no async complexity; bounded by OS thread limits |
| No recursive spawning | No | Prevents unbounded fan-out; parent controls orchestration |
| No direct subagent-to-subagent comms | No | Keeps communication parent-mediated; avoids races |
| Inherit approvals by default | Yes | Safety first; human sees which subagent needs approval |
| Independent budgets | Yes | Parent budget unaffected by subagent usage |
| Plain text results | Yes | Flexible; structured output is a prompt convention, not a schema |
| Subagent prompt derived | Yes | Role awareness + no slash commands + no parent session access |
| No automatic cleanup | No | Parent decides when to delete subagent dirs; audit trail preserved |
| Max concurrent subagents | Configurable, default 5 | Prevents resource exhaustion; tunable per use case |

## 15. File changes summary

| File | Change |
|------|--------|
| `harness/tools.py` | Add `spawn_subagent`, `wait_subagent`, `list_subagents` definitions + executors; extend `classify_source` and `source_gate` for subagent paths |
| `harness/loop.py` | Add `Subagent` class (thread wrapper); add `spawn_subagent`, `wait_subagent`, `cancel_subagent` methods to `Loop`; extend `run_turn` to track subagents |
| `harness/session.py` | Extend `init_layout` for subagent mode; add subagent session dir creation |
| `harness/config.py` | Add `subagent_approvals`, `subagent_max_concurrent`, `subagent_budget_fraction` config options |
| `harness/approvals.py` | Pass subagent ID through approval flow |
| `harness/usage.py` | Subagents get independent `UsageTracker` instances |
| `harness/__main__.py` | Wire subagent tools into the REPL/TUI; show subagent status in output |
| `harness/tui/` | (Phase 2+) Add subagent status pane |
| `tests/test_subagents.py` | New test file |
