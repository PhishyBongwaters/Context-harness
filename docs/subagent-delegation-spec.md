# Subagent Delegation — specification

**Status:** draft (2026-10-09)
**Supersedes:** `docs/subagent-execution-spec.md` (too much machinery:
parallel fan-out, result-passing protocol, registry; this spec does
one thing — delegate to a single subagent — and does it with the
tools the harness already has)

## 1. What this is and why

The harness runs a single agent loop. **Delegation** lets the model hand
one self-contained subtask to a **subagent**: a full harness loop running
in its own session directory, on its own budget, in a background thread.

The key design decision: **the parent is pointed at the result file, not
fed the result.** `delegate()` returns a path. The parent reads it with
the existing `read` tool when it's ready, taking only what it needs.
No result-passing protocol, no serialization format, no parent-context
bloat — the subagent's 20 steps of tool noise never enter the parent's
transcript.

One subagent at a time. No orchestration framework, no fan-out, no
registry. If one-at-a-time proves useful, parallelism is a later spec.

## 2. The three tools

### 2.1 `delegate`

```json
{
  "name": "delegate",
  "description": "Hand a self-contained subtask to a subagent running in the background. Returns immediately with the path to the result file — read it with the read tool when the subagent is done (see wait_subagent). Only one subagent runs at a time.",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {
        "type": "string",
        "description": "The subtask. Be specific and self-contained — this becomes the subagent's entire context (its sats/current.md)."
      },
      "budget_hard": {
        "type": "integer",
        "description": "Subagent hard budget in tokens (default: 1/4 of the parent's hard budget)."
      },
      "model": {
        "type": "string",
        "description": "Model override (default: inherit parent's model)."
      },
      "tools": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tool allowlist (default: read, edit, exec, tokens). Narrow it for safety, e.g. [\"read\", \"tokens\"] for research."
      }
    },
    "required": ["task"]
  }
}
```

Returns immediately:

```json
{"status": "running", "result_path": "<session>/subagents/9f3a/result.md"}
```

If a subagent is already running: `{"status": "error", "message":
"a subagent is already running — wait for it or cancel it first"}`.

### 2.2 `wait_subagent`

```json
{
  "name": "wait_subagent",
  "description": "Block until the running subagent finishes (or timeout). Returns the result path — read the file yourself; the content is NOT inlined.",
  "parameters": {
    "type": "object",
    "properties": {
      "timeout": {
        "type": "integer",
        "description": "Seconds to wait (default: 120). 0 = check once, don't block."
      }
    }
  }
}
```

Returns:

```json
{"status": "completed", "result_path": "<session>/subagents/9f3a/result.md"}
```

or `{"status": "running", "result_path": "..."}` on timeout, or
`{"status": "failed", "result_path": "...", "error": "..."}`.

The result file exists in all terminal states (completed/failed/
cancelled) — it always says what happened.

### 2.3 `cancel_subagent`

No parameters. Sets the stop event on the subagent thread (same
`TurnInterrupted` mechanism as the parent's own interrupt). The
subagent finishes its current step, writes `result.md` with
`status: "cancelled"`, and exits. Returns `{"status": "cancelled"}`.

## 3. Lifecycle

1. Parent calls `delegate(task)`.
2. Harness creates `<session>/subagents/<uuid>/` with the blank-slate
   layout (§5), sets `sats/current.md` to the task, writes the subagent
   `prompt.md` (§6).
3. Harness starts one background thread running a `Loop` on the
   subagent session dir. Returns `{status, result_path}` immediately.
   The parent's turn continues — it can do other work, or call
   `wait_subagent`.
4. The subagent runs a normal harness turn: assemble → model → tools →
   episode close → repeat until its reply has no tool calls.
5. On termination (done, budget-exhausted, failed, cancelled, timeout),
   the harness writes `result.md` (§7) and clears the slot. The parent
   can `delegate()` again.
6. Parent calls `wait_subagent()`, gets `completed`, then `read`s
   `result_path` — pulling only the findings it needs into its own
   context via the normal edit flow.

## 4. Why one at a time

- No registry, no ID plumbing in tool signatures, no max-concurrent
  config, no budget-oversubscription math (1/4 × 5 = 125% was the old
  spec's bug).
- Approval UX stays sane: at most one `[subagent]` modal at a time.
- The parent model reasons about one background task, not a fleet.
- Parallelism, if ever needed, is a separate spec that builds on this
  one's session layout and result-file contract.

## 5. Subagent session layout

```
<sessions>/<parent_id>/subagents/<uuid>/
  prompt.md      # subagent prompt (§6), harness-written
  state.json     # harness-owned
  index.md       # harness-owned
  history.md     # model-editable (subagent's own)
  sats/
    current.md   # the delegated task (harness-seeded, model-editable)
    goals.md     # seeded: "complete the task in current.md"
    facts.md     # subagent's discoveries
    decisions.md # subagent's decisions
    tasks.md     # subagent's breakdown
  scratch.md     # harness-owned
  archive/       # harness-owned
  context.md     # harness-owned build artifact
  result.md      # harness-written on termination (§7)
```

Never the `.current` session. Never listed by `/list`.

## 6. Subagent prompt

`prompt.md` is the parent's prompt with a fixed prefix and three
deletions:

```
You are a subagent inside a context-as-file harness, working for a
parent agent. Your task is in sats/current.md. Your final reply will
be saved to result.md for the parent — be concise, lead with findings,
skip narration of your process.
```

Deletions: no slash-command docs (`/new`, `/open`, `/list`, `/quit`);
no `delegate`/`wait_subagent`/`cancel_subagent` tools (no recursion);
budget section shows the subagent's own limits.

## 7. `result.md`

Harness-written on termination. Always exists in a terminal state:

```markdown
# Subagent result — <uuid>

- task: <first line of sats/current.md>
- status: completed | failed | cancelled
- steps: 7
- tokens: 12345
- ended: 2026-10-09T14:30:00-03:00

## Findings

<the subagent's final reply, verbatim>
```

On `failed`, a `## Error` section replaces Findings. The parent reads
this file with the existing `read` tool — `classify_source` already
permits it (`subagents/...` → `other-session` kind → read allowed).
No gate changes needed for the parent.

## 8. Budgets

Independent. `budget_hard` defaults to 1/4 of the parent's hard
budget; `budget_soft` defaults to 80% of the subagent's hard budget.
The parent's tracker never sees subagent tokens. A subagent that blows
its budget fails alone — `result.md` says `failed`, the parent decides
what to do (retry with a narrower task, skip, adapt).

## 9. Approvals

The subagent inherits the parent's approver. Mutating tool calls pop
the normal modal with a `[subagent]` prefix in the title so the human
knows the source. One subagent → at most one pending approval → no
modal pile-up. (`--yes` auto-approves subagents exactly like the
parent.)

## 10. Source gates for the subagent

The subagent's `session_dir` is its own `subagents/<uuid>/`, so its
own files classify normally. One new rule: the subagent may not touch
the parent's session dir. Extend `classify_source`/`source_gate` so
that when the resolved target is under the parent's session dir but
outside the subagent's own subdir, `read`/`write`/`edit` are all
DENIED. The subagent's allowed world: its own session dir + the
configured `workdir`. (The parent side needs no changes — §7.)

## 11. Implementation plan

### Phase 1 — Session + thread
- **D1** `session.py`: `init_subagent_session(parent_dir, task)` —
  creates `subagents/<uuid>/`, blank-slate layout, seeds
  `sats/current.md` + `sats/goals.md`, writes subagent `prompt.md`.
- **D2** `loop.py`: `Loop.run_subagent(sdir, cfg)` — a `Loop` bound to
  the subagent dir/budget/tools; parent holds one
  `self._subagent = {thread, sdir, result_path} | None`.
- **D3** Termination: on loop exit, harness writes `result.md` (§7),
  clears the slot. All exit paths (done/budget/fail/cancel/timeout)
  covered.

### Phase 2 — Tools + gates
- **D4** `tools.py`: `delegate`, `wait_subagent`, `cancel_subagent`
  definitions + executors. `delegate` refuses when a subagent runs.
- **D5** `tools.py`: subagent-side gate (§10). Parent side unchanged.
- **D6** `approvals.py`: `[subagent]` prefix on modal titles for
  subagent-originated requests.

### Phase 3 — Config + verification
- **D7** `config.py`: `subagent_budget_fraction` (default 0.25).
  Model override passes through existing config plumbing.
- **D8** Tests: layout seeding, gate denials (subagent→parent),
  result.md written on all terminal states, delegate-refuses-while-
  running, parent can read result_path.
- **D9** Manual: delegate a real research task, `wait_subagent`,
  `read` the result file, confirm the parent's transcript never saw
  the subagent's tool noise.

## 12. Open questions

1. **Wall-clock timeout?** The old spec had one (default 300s). A
   hung model call blocks `wait_subagent` indefinitely. Add
   `timeout_s` to `delegate` (default: none) or leave it to the
   parent to `cancel_subagent`? Leaning: leave it out; the parent is
   the timeout.
2. **TUI visibility.** Show `SUBAGENT: running…` in the charm strip
   while active. Cheap, high-value — do it in Phase 2.
3. **Retry helper?** If a subagent fails, the parent re-delegates
   manually with a narrower task. No auto-retry — the parent is the
   retry policy.

## 13. Explicit non-goals

Parallel subagents. Subagent-to-subagent communication. Structured
result schemas (the parent parses prose; it has a whole model for
that). Auto-cleanup of `subagents/` (audit trail stays; the parent
can `exec rm -rf` when done). Recursive delegation.
