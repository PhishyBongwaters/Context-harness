# Session Notes: t0002–t0015
## Purpose: Review material for team on subagent orchestration

## Key Findings

### 1. Current subagent interface
- delegate(task, budget_hard, model, tools) → returns result.md path + session_dir
- One subagent at a time (sequential only)
- Subagent runs in isolated context — zero impact on main context budget
- Result is a file; read it after wait_subagent says done
- On failure, check session_dir/history.md for trace

### 2. What worked in this session
- Subagent successfully scanned D:\Projects, found largest repo (ROOP-PHISHY, 20.26GB), identified most common extensions (.jpg, .py)
- Zero context cost for the subagent's work
- Main agent only paid for: delegate call + wait + read result

### 3. Strategic subagent orchestration — the gap
Current: one-shot delegate → wait → read result
Desired: pipeline where main agent:
  a) Breaks goal into subtasks
  b) Delegates each sequentially
  c) Makes decisions between subagents based on results
  d) Delegates follow-up tasks based on what was learned
  e) Synthesizes final output

### 4. What's already possible
- Sequential delegation with decision points (one at a time, but you can decide between each)
- Subagents can use tools (read, edit, exec, tokens by default)
- Results are structured files you can read and reason about
- session_dir/history.md gives you the subagent's full trace if something went wrong

### 5. What would make it better
- **Structured task descriptions**: ability to specify output format expectations
- **Chaining**: result of subagent N feeds into task description for subagent N+1
- **Conditional branching**: "if result contains X, delegate Y; otherwise delegate Z"
- **Batch mode**: "run these 3 subtasks, return all results" (even if sequential internally)
- **Progress tracking**: subagent reports intermediate results, not just final output
- **Error recovery**: if subagent fails, auto-retry with adjusted task description

### 6. The "hang out til it's done" pattern
Main agent delegates, waits, reads result, decides next step. This is already the flow. The formalization is about:
- Making the task descriptions more structured (so subagents produce consistent output)
- Making the decision logic more explicit (so the harness can help orchestrate)
- Making the result format more machine-readable (so the main agent can parse it programmatically)

### 7. Context management insight
The harness already handles context well — sats files are one-liners, history is pruned, scratch is per-episode. The bottleneck was always the main agent's context budget eating itself on tool calls. Subagents solve this by moving tool calls off to isolated workers.

### 8. Key quote from session
"the harness handles keeping active context window clean while saving working knowledge to files" — user
"you are actively helping develop the tool" — user

## Recommendations for Team
1. Formalize a "pipeline" mode for subagents: define tasks, delegate sequentially, collect results
2. Add structured output format to delegate() — e.g., "return JSON with fields: findings, blockers, next_steps"
3. Add conditional delegation support — harness evaluates result and picks next task
4. Consider a "supervisor" role for the main agent: it doesn't do the work, it delegates, reviews, and decides
5. The one-at-a-time constraint is fine for orchestration — it's actually a feature, not a limitation. Parallel fan-out without coordination is chaos.
