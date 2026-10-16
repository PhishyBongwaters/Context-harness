# Agent Self Review of Context-Harness

User: 
I want you to think about the process that let you chew through the linux kernel, would provide benefit to a "update this process" type request.  i.e. I would see it basically as a "plan first, code knowledgably" approach.  subagent is there but has limitations and drawbacks, the harness context control is so good it probably removes the need at all to delegate.

assistant:
Good observation. Let me think about what actually made the kernel trace work well, and what a generalized "analyze → spec → execute" process would look like.

## What worked in the kernel trace

The key wasn't delegation — it was **progressive depth with context discipline**:

1. **Top-down skeleton first** — I started by mapping the syscall entry point and the high-level call chain (11 layers, one line each). This gave me a coordinate system.
2. **Layer-by-layer deepening** — Each layer got its own pass: function name, file, line number, what it does, what it calls next. No jumping around.
3. **Blocking points tracked separately** — Instead of mixing "what does this function do" with "where can it sleep," I kept them as a parallel concern. That's why the final summary had 12 distinct blocking points cleanly enumerated.
4. **Context-aware curation** — The sats carried the durable state (call chain, files, blocking points). History carried the reasoning. Scratch carried working notes. Each turn's handoff was tight — under 40 lines, no redundant copies.
5. **No premature optimization** — I didn't try to solve the whole thing in one pass. Each turn added one or two layers, verified the chain, and moved on.

## The generalized process

For a "modify system X" request, the process would be:

### Phase 1: Top-level map (analyze mode)
- Identify the system's entry points and boundaries
- List the major components/layers
- Note the data flow and control flow at a high level
- Output: a skeleton call graph or component diagram

### Phase 2: Layer-by-layer deepening
- Pick one layer, trace it fully: functions, files, dependencies
- Identify side effects, mutations, and blocking points
- Move to the next layer
- Output: annotated call chain with file locations

### Phase 3: Spec generation
- Synthesize the deep trace into a concrete modification plan
- List every file that needs changes
- List every function that needs modification or replacement
- Note edge cases, invariants, and test points
- Output: a spec clear enough for an agent to execute without ambiguity

### Phase 4: Execution
- Execute the spec, layer by layer
- Verify each change before moving on
- Update sats as you go (current.md tracks progress, tasks.md tracks open items)

### Where delegation fits (or doesn't)

Delegation is useful when:
- A single layer is so large it would blow your context (e.g., "trace the entire VFS layer")
- You need parallel analysis of independent subsystems
- The reading workload is purely exploratory (no synthesis needed yet)

But the harness context control *does* make delegation less necessary because:
- The handoff mechanism preserves state across turns without context bloat
- Curation keeps the assembled context tight
- You can do deep sequential analysis turn-by-turn without losing state

The real constraint isn't context size — it's **context coherence**. A subagent's output is a file you have to read, which doubles your context. The handoff mechanism avoids this by keeping state in the sats, which are assembled efficiently.

## What a "update this process" request would look like

If you said "upgrade the auth module in system X," I'd:

1. **Phase 1:** Map the auth flow — entry points (login, token refresh, session validation), components (auth handler, token store, permission checker), data flow (credentials → validation → session → access)
2. **Phase 2:** Deepen each component — trace `authenticate()` through every function, note where it reads/writes state, where it calls external services, where it can fail
3. **Phase 3:** Spec — "Change token validation from X to Y. Files: auth/token.go, auth/session.go, middleware/auth.go. Functions: validateToken(), refreshSession(). Edge cases: expired tokens, concurrent refresh, clock skew."
4. **Phase 4:** Execute — make the changes, verify each one, update tests

The difference from the kernel trace is that the kernel was **read-only analysis** (no code changes), while a modification request adds a verification step after each change.

Does this match what you had in mind? Or were you thinking of a different structure?
