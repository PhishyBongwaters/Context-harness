# Goal-driven auto-continue — specification

Status: **implemented** on `feat/handoff-per-turn` (12 tests in
`tests/test_auto_continue.py`). Follow-on to the handoff-per-turn
feature (issue #5).

## Problem

Every turn starts with a user message (`run_turn` is only ever invoked
from user input; there is no auto-continue anywhere in the codebase).
When a goal is still `- [active]` at turn end, the agent has more work
to do but the harness waits. The user must nudge ("keep going") to
start each turn. For long-horizon tasks this is pure friction: the
nudges aren't a defect, they're the turn-starting mechanism, but they
shouldn't be necessary when the objective is already on record.

The handoff makes auto-continue safe: each new turn assembles the
handoff first, so an automatically started turn resumes correctly
instead of amnesiacally. Auto-continue is the payoff of the handoff.

## Proposal

When a turn ends and any goal in `sats/goals.md` is still `- [active]`,
the harness automatically begins another turn instead of waiting for
user input — up to a cap. One loop, one rule: the auto-turn is a full
`run_turn` (assembly, budget check, model steps, handoff validation,
episode close), not a lighter path.

### Trigger

After `_close_episode` in `run_turn`, if all of the following hold:

1. `auto_continue` is enabled (config; default **off**),
2. `sats/goals.md` contains at least one `- [active]` line,
3. fewer than `auto_continue_max_turns` auto-turns have run since the
   last user message,

then the harness starts a new turn immediately.

### The synthesized message

The new turn needs a `user_text`. The harness synthesizes one,
recorded verbatim in history:

```
[auto-continue 1/3] Active goal: <first active goal text>.
Continue working it; your handoff's Next says where.
```

The counter (`1/3`) is the auto-turn index within the current chain.

### Transcript honesty

Harness-initiated turns are labeled distinctly in `history.md`:

```
## auto t0016
```

instead of `## user t0016`. The record must always distinguish
user-initiated from harness-initiated turns. No hidden behavior: the
TUI shows auto-turns streaming exactly like user turns, with an
auto-continue indicator and the chain position (`auto 1/3`).

### Stop conditions

The chain stops when any of these is true:

- No `- [active]` goals remain (all `- [done]`).
- `auto_continue_max_turns` reached.
- The user interrupts (Ctrl+C / stop) — interrupts the whole chain,
  not just the current turn.
- The user sends a message — it takes precedence; the chain ends and
  the auto counter resets.
- Budget exhausted (existing per-turn budget behavior applies).

A denied tool approval does **not** stop the chain by itself; approvals
behave exactly as they do today (the turn waits). If the wait is
inappropriate for an unattended chain, that is a separate piece.

### Budget and approvals

Auto-turns consume budget exactly like user turns (same per-turn
budget, same usage display). The cap bounds the total: worst case per
user message is `1 + auto_continue_max_turns` turns. No new budget
machinery.

### Configuration

```ini
auto_continue = false          # default off; existing behavior unchanged
auto_continue_max_turns = 3    # cap per user message
```

Session flags `--auto-continue` / `--no-auto-continue` override the
config for one session. (Per-goal opt-in, e.g. `- [active] [auto]`,
is a possible future refinement; v1 is the global flag.)

### Why the default is off

Auto-continue spends the user's budget and model calls without them
present. That must always be an explicit opt-in. The default preserves
today's behavior exactly.

## Relationship to the handoff and the GOALS paragraph

- The handoff is what makes each auto-turn coherent (resume from
  Now/Next/Blockers/Pointers).
- The GOALS prompt paragraph (standing order: prefer tool calls while
  a goal is `[active]`) is what makes each auto-turn *productive*
  within its 50 steps.
- Auto-continue is what removes the *between*-turn nudge.

The three compose: handoff carries the thread, GOALS keeps the turn
working, auto-continue starts the next turn.

## Out of scope for v1

- Subagent auto-continue (each `delegate` is one `run_turn` today;
  revisit when subagents go multi-turn).
- Per-goal `[auto]` opt-in markers.
- Unattended-approval policy (what auto-turns may approve while the
  user is away).

## Acceptance criteria

1. With `auto_continue` on and an `[active]` goal at turn end, a new
   turn starts with no user input, up to the cap.
2. Auto-turns appear in `history.md` as `## auto tNNNN`, never as
   `## user tNNNN`.
3. The chain stops on: all goals `[done]`, cap reached, user
   interrupt, user message, budget exhaustion.
4. Each auto-turn validates the handoff exactly like a user turn
   (no exemption, no lighter path).
5. Default off: with `auto_continue` unset, behavior is byte-identical
   to today.
6. Usage display accounts auto-turns visibly (chain position shown).

## Open questions

- Should the synthesized message carry the full goal text or just
  name it? (Spec proposes full text of the first active goal.)
- If several goals are `[active]`, name all of them or just the
  first? (Spec proposes the first; the handoff's Next covers the
  rest.)
- In serve/Discord mode, should auto-continue apply the same way?
  (It goes through the same `run_turn`, so mechanically yes; the
  UX implications are unexamined.)
