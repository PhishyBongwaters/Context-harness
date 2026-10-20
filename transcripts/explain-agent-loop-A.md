## sat current
# Current task

What you are actively working on right now, one line. Update it when the task changes or completes.

## sat goals
# Goals

Enduring objectives, one per line: `- [active] ...` / `- [done] ...`.

## sat facts
# Facts

Discovered truths about the world, one line each.

## sat decisions
# Decisions

Choices made and why, one line each.

## sat tasks
# Tasks

Open work items, one line each.

# History

Curated durable transcript. Edit with the edit tool (exact old_string /
new_string). Sections: `## user t<NNNN>`, `## assistant t<NNNN>`,
`## episode t<NNNN>`.
## user t0001
Review the code in D:/projects/context-harness until you can cleanly explain how the agent loop works to me

## assistant
I'll review the codebase to understand how the agent loop works.
```tool-calls
[{"id": "I6jUuB8u8B926okUoWxTiHTMa3j3IMGl", "name": "list_dir", "arguments": {"path": "D:\\projects\\context-harness", "recursive": false}}]
```
## tool I6jUuB8u8B926okUoWxTiHTMa3j3IMGl
D:\projects\context-harness  [17 entries]
dir   -p/
dir   .debug/
file  .env  165B  2026-10-05 15:35
file  .env.example  214B  2026-10-05 15:33
dir   .git/
file  .gitignore  541B  2026-10-05 08:13
dir   .pytest_cache/
dir   docs/
file  dump_ctx.py  2279B  2026-10-08 10:17
file  eval_out.txt  764B  2026-10-10 16:22
dir   evals/
dir   harness/
file  README.md  18432B  2026-10-10 06:58
file  requirements-tui.txt  91B  2026-10-06 12:21
file  requirements.txt  166B  2026-10-05 08:01
file  test_out.txt  25602B  2026-10-10 16:18
dir   tests/
## assistant

```tool-calls
[{"id": "SdLglisdp7rn3Iqlf834wkuodovAtwTg", "name": "list_dir", "arguments": {"path": "D:\\projects\\context-harness\\harness", "recursive": true}}, {"id": "kSNGnmzeZXAEpJBkK0faSMTRVscNU8at", "name": "read", "arguments": {"path": "D:\\projects\\context-harness\\README.md"}}]
```
## tool SdLglisdp7rn3Iqlf834wkuodovAtwTg
D:\projects\context-harness\harness  [55 entries]
file  __init__.py  313B  2026-10-05 08:01
file  __main__.py  61039B  2026-10-10 14:10
dir   __pycache__/
file  __init__.cpython-312.pyc  474B  2026-10-05 08:01
file  __init__.cpython-314.pyc  466B  2026-10-05 08:07
file  __main__.cpython-312.pyc  8704B  2026-10-05 08:01
file  __main__.cpython-314.pyc  81996B  2026-10-10 14:10
file  approvals.cpython-314.pyc  24709B  2026-10-10 16:12
file  assembly.cpython-314.pyc  5822B  2026-10-10 14:10
file  config.cpython-314.pyc  39297B  2026-10-10 14:10
file  context.cpython-312.pyc  11848B  2026-10-05 08:01
file  context.cpython-314.pyc  19929B  2026-10-10 06:58
file  debug.cpython-314.pyc  3749B  2026-10-05 08:14
file  deterministic.cpython-314.pyc  19151B  2026-10-10 06:58
file  loop.cpython-312.pyc  12637B  2026-10-05 08:01
file  loop.cpython-314.pyc  58942B  2026-10-10 14:10
file  project.cpython-314.pyc  4962B  2026-10-05 10:41
file  providers.cpython-312.pyc  12442B  2026-10-05 08:01
file  providers.cpython-314.pyc  20373B  2026-10-10 07:10
file  session.cpython-314.pyc  19590B  2026-10-10 06:58
file  spinner.cpython-314.pyc  4052B  2026-10-05 11:37
file  tools.cpython-312.pyc  6925B  2026-10-05 08:01
file  tools.cpython-314.pyc  22012B  2026-10-10 06:58
file  usage.cpython-314.pyc  5204B  2026-10-05 09:29
file  approvals.py  14974B  2026-10-10 16:11
file  assembly.py  4185B  2026-10-10 14:10
file  config.py  34909B  2026-10-10 14:10
file  context.py  12447B  2026-10-10 06:58
file  debug.py  1536B  2026-10-05 08:13
file  deterministic.py  13669B  2026-10-10 06:58
file  loop.py  52766B  2026-10-10 14:10
file  project.py  2407B  2026-10-05 10:40
file  providers.py  13403B  2026-10-10 07:10
file  serve.py  4461B  2026-10-10 14:10
file  session.py  13870B  2026-10-10 06:58
file  spinner.py  1827B  2026-10-05 11:37
file  tools.py  18902B  2026-10-10 06:58
dir   tui/
file  __init__.py  438B  2026-10-06 12:21
dir   __pycache__/
file  __init__.cpython-314.pyc  822B  2026-10-06 12:26
file  app.cpython-314.pyc  105967B  2026-10-10 14:10
file  approvals.cpython-314.pyc  5448B  2026-10-06 12:26
file  bridge.cpython-314.pyc  4151B  2026-10-10 06:58
file  commands.cpython-314.pyc  3763B  2026-10-06 12:26
file  lcars.cpython-314.pyc  30119B  2026-10-10 06:58
file  widgets.cpython-314.pyc  59365B  2026-10-10 06:58
file  app.py  71324B  2026-10-10 14:10
file  approvals.py  3448B  2026-10-06 12:21
file  bridge.py  2754B  2026-10-10 06:58
file  commands.py  2599B  2026-10-06 12:21
file  lcars.py  18216B  2026-10-10 06:58
file  widgets.py  38454B  2026-10-10 06:58
file  usage.py  2473B  2026-10-05 09:27
file  user_tools.py  5300B  2026-10-10 14:10
## tool kSNGnmzeZXAEpJBkK0faSMTRVscNU8at
D:\projects\context-harness\README.md  [lines 1-200 of 408]
     1  # context-harness
     2  
     3  Daily-driver agent loop with deterministic, blank-slate context assembly.
     4  
     5  ## Why
     6  
     7  Every agent harness fights the same enemy: context bloat. Transcripts
     8  accumulate — the system prompt echoed back, tool outputs piling up,
     9  old turns lingering — until the window fills, inference slows, costs
    10  climb, and the model loses the thread.
    11  
    12  The usual fixes are bad. Naive truncation silently drops information.
    13  Model-driven summarization is a guess dressed as strategy: a janitor
    14  model can't tell what's load-bearing.
    15  
    16  This harness takes the other path: **the context is rebuilt from
    17  scratch every turn, deterministically, by the harness — never by the
    18  model.** Tool traces live exactly one turn, then are archived. History
    19  is bounded by automatic windowing. What stays in context is a
    20  mechanical decision, not a judgment call. A 50k-token working turn
    21  collapses to ~2k the next turn, and anything archived can be re-read
    22  deliberately.
    23  
    24  There is no accumulating transcript. Every turn the harness assembles
    25  the model's context from sources, in fixed order:
    26  
    27  1. **satellites** — `sats/current.md`, `sats/goals.md`,
    28     `sats/facts.md`, `sats/decisions.md`, `sats/tasks.md`,
    29     `sats/hypotheses.md`
    30     (durable one-liners, sent as `## sat <name>` sections)
    31  2. **history** — `history.md` (stamped conversation record plus episode
    32     pointers)
    33  3. **scratch** — `scratch.md` (the current episode's working notes:
    34     assistant replies and tool results)
    35  
    36  The prompt loads fresh from `prompt.md` through the API system parameter
    37  each turn. The assembled text is written to `context.md` as an
    38  inspectable build artifact — rebuilt every turn. The model neither
    39  reads nor edits it: the assembled text is already its injected
    40  messages. No hidden transcript, no special compact tool.
    41  
    42  ## Who owns what
    43  
    44  **The harness owns:** prompt loading, assembly order, turn/section
    45  counters (`state.json`), the episode lifecycle, archive pointers,
    46  source-gate enforcement, pre-edit backups, and verification. Context
    47  management is deterministic harness behavior, not model judgment.
    48  
    49  **The model may edit** (exact `old_text`/`new_text`, must match exactly
    50  once):
    51  - `history.md` — the conversation record (`## user t<NNNN>`,
    52    `## episode t<NNNN>`)
    53  - `sats/current.md` — the active task (update when it changes)
    54  - `sats/goals.md` — enduring objectives (`- [active]` / `- [done]`);
    55    add one with `/goal "text"` or `--goal "text"`
    56  - `sats/facts.md`, `sats/decisions.md`, `sats/tasks.md` — one-line
    57    facts, decisions with reasons, open tasks
    58  - `sats/hypotheses.md` — half-formed ideas, unproven suspicions
    59    (promote to facts when confirmed, delete when disproven)
    60  
    61  `write` is rejected on all session sources — use `edit`.
    62  
    63  **Harness-owned (model read-only):** `prompt.md`, `state.json`,
    64  `index.md`, `scratch.md`, `archive/`, `context.md`. `state.json`
    65  can't even be read. Denied calls return a `DENIED` tool result the
    66  model must respect.
    67  
    68  ## Episodes
    69  
    70  Tool calls and their results accumulate in `scratch.md` during a turn.
    71  When the assistant replies with no more tool calls, the episode closes:
    72  the harness archives scratch to `archive/<date>-t<NNNN>.md`, clears it,
    73  and appends a `## episode t<NNNN>` pointer to history (archive path,
    74  turn range, tool-call count). The closing reply is recorded to history
    75  automatically. Archived detail stays out of hot context but can be
    76  re-read deliberately by path — visible cost, no silent bloat.
    77  
    78  ## Budget
    79  
    80  A token budget meter shows every turn. Soft breach warns; hard breach
    81  gives the model a curation turn. Deterministic stages run first
    82  (exact-dupe collapse, oldest-tool eviction, per-section caps — all on
    83  `scratch.md`, milliseconds, no model calls); if still over, the model
    84  gets edit-only curation turns on `history.md`/`sats/*.md` until the
    85  next assembled request fits.
    86  Enforcement sits at 50% of the model's context window so pruning starts
    87  early and the prune request itself always fits; soft warns at 80% of
    88  hard. The header shows both numbers, e.g. `window=135,168 [live]
    89  prune-at=67,584` — the window is the context, the prune trigger is not.
    90  Curation fails repeatedly → loud error, never silent truncation. Every
    91  source edit backs up the file first
    92  (`history.pre-edit-<ts>.bak`), so all model curation is reversible. The
    93  curation turn can run on a separate janitor model — cheaper/smaller, even
    94  local while the main model is cloud (see `prune_*` config).
    95  
    96  Every model edit of a source prints a mechanical diff: which sections
    97  were removed/added and how many tokens were recovered. You always
    98  see what the model threw away.
    99  
   100  Design follows the Context Language Models paper (arXiv:2609.37725),
   101  implemented from scratch for interactive use.
   102  
   103  ## Session layout
   104  
   105  ```
   106  <sessions>/<id>/
   107    prompt.md        system prompt (harness-owned; loaded fresh each turn)
   108    state.json       turn/section counters (harness-owned, unreadable)
   109    index.md         pointer map (harness-owned)
   110    history.md       stamped conversation record (model-editable)
   111    sats/            current.md, goals.md, facts.md, decisions.md,
   112                     tasks.md, hypotheses.md (model-editable)
   113    scratch.md       current episode working notes (harness-owned)
   114    archive/         closed episodes, one file per turn (harness-owned)
   115    context.md       assembled artifact for inspection (rebuilt every turn)
   116  ```
   117  
   118  Old single-file sessions migrate automatically on open: user sections
   119  become stamped history, each turn's assistant/tool run becomes an
   120  archived episode, the turn counter is seeded, and the original is kept
   121  as `context.md.pre-migration-<ts>.bak`. Ambiguous transcripts refuse
   122  to migrate loudly instead of guessing.
   123  
   124  ## Run
   125  
   126  ```bash
   127  pip install -r requirements.txt
   128  python -m harness --config        # writes platform-default config.json
   129  python -m harness "summarize this repo"
   130  python -m harness                 # REPL, continues current session
   131  python -m harness --new "task"    # fresh session
   132  python -m harness --list          # list sessions
   133  python -m harness --tui          # terminal UI (needs the optional extra)
   134  ```
   135  
   136  The REPL stays in its session: plain text appends to the same
   137  `context.md`. Slash commands (also in `/help`):
   138  
   139  - `/list` — show sessions (`*` = current)
   140  - `/new` — fresh session and switch to it
   141  - `/new summarize this repo` — fresh session + run that task immediately
   142  - `/open 20261005-0826` — switch back (full id or unambiguous prefix)
   143  - `/project [name]` — show/switch project
   144  - `/usage [N]` — ledger totals + last N calls (default 5)
   145  - `/config` — show effective config (redacted)
   146  - `/providers` — list known providers
   147  - `/models` — list models from current provider (OpenAI-compatible)
   148  - `/quit` — leave (empty line also quits)
   149  
   150  Flow: `/new` once, then just type. Only `/new` mints a new
   151  `sessions/<id>/context.md`; everything else appends to the current one.
   152  
   153  ## TUI (optional)
   154  
   155  The CLI is the default interface and works with stdlib-only deps.
   156  The TUI is the same agent loop with a terminal UI — install the extra:
   157  
   158  ```bash
   159  pip install -r requirements-tui.txt   # textual
   160  python -m harness --tui
   161  ```
   162  
   163  Same session, same provider registry, same ledger as the CLI.
   164  
   165  - transcript pane (assistant text, tool calls, budget/prune lines),
   166    budget bar, status line, input box
   167  - chat-style message panels: blue for you, dark for assistant, black for
   168    tools — click any message to copy its full text to the clipboard
   169  - mutating tools pop an approval dialog: `a` turn / `s` session /
   170    `d` or `esc` deny, countdown default-deny (same timeout as CLI)
   171  - `ctrl+s` session picker, `ctrl+o` provider/model picker,
   172    `ctrl+d` debug-log tail, `ctrl+e` export transcript to a file
   173  - `ctrl+c` copies the current selection (never quits — quit is `/quit`
   174    or `ctrl+q`)
   175  - slash commands work in the input box exactly like the REPL
   176    (`/new`, `/open`, `/list`, `/project`, `/usage`, `/config`,
   177    `/providers`, `/models`, `/help`, `/quit`)
   178  - `--no-mouse` leaves selection to the terminal instead (keyboard
   179    still drives everything in-app)
   180  - turn errors tee to `<session-dir>/tui-errors.log` with tracebacks
   181  
   182  ## Requirements
   183  
   184  - Python 3.10+
   185  - `tiktoken` (pinned in `requirements.txt`) — the budget meter counts
   186    with cl100k_base on every machine, so enforcement is consistent. First
   187    run downloads the BPE file once (needs internet; set `TIKTOKEN_CACHE_DIR`
   188    to control where it lands).
   189  
   190  ## Config
   191  
   192  Platform-default location (`%APPDATA%/context-harness/config.json` on
   193  Windows, `~/.config/context-harness/config.json` elsewhere):
   194  `provider` (`openai` / `anthropic` / `nvidia`), `model`, `base_url` (any
   195  OpenAI-compatible `/v1`, including local servers), `budget_hard` /
   196  `budget_soft`, `approval_timeout`, `exec_timeout`, `exec_timeout_max`,
   197  `request_timeout`, `usage_note`, `prune_target`, `prune_keep_tools`,
   198  `prune_section_cap`. API key comes from the environment (`api_key_env`) —
   199  never from the file. `sessions_dir` unset selects the platform default
   200  (`%LOCALAPPDATA%` on Windows).
... (408 total lines)
## assistant

```tool-calls
[{"id": "VOFp2WIU18OQ8pkG1P2VKWtSUG58SFEv", "name": "read", "arguments": {"path": "D:\\projects\\context-harness\\harness\\loop.py"}}]
```
## tool VOFp2WIU18OQ8pkG1P2VKWtSUG58SFEv
D:\projects\context-harness\harness\loop.py  [lines 1-200 of 1089]
     1  """Main agent loop: the context file IS the transcript (CLM-style).
     2  
     3  Before every model call the harness reads context.md and parses it into
     4  messages. After each response it appends the assistant section and tool
     5  results to the file. The model restructures the file at any time with its
     6  ordinary write/edit tools; the next read picks the edits up.
     7  
     8  Budget enforcement:
     9    - soft breach -> warning event, turn continues
    10    - hard breach -> the model gets a prune-only turn (write/edit on the
    11      context file only, ephemeral transcript) until usage is back under the
    12      hard limit. The harness never silently truncates; if pruning fails
    13      after N attempts it raises loudly.
    14  """
    15  from __future__ import annotations
    16  
    17  import json
    18  import threading
    19  from dataclasses import dataclass, field
    20  from pathlib import Path
    21  
    22  from .assembly import assemble, load_prompt, write_assembled
    23  from .context import (Budget, ContextFile, _split_atem_xml, count_tokens,
    24                        diff_transcripts, parse_transcript, render_assistant,
    25                        render_history_user, render_tool,
    26                        sanitize_assistant_content)
    27  from .session import next_turn
    28  from .approvals import (EXEC_TIMEOUT_DEFAULT, EXEC_TIMEOUT_MAX, Approver,
    29                         Policy, clamp_exec_timeout)
    30  from .deterministic import (DEFAULT_KEEP_RECENT_TOOLS, DEFAULT_SECTION_CAP,
    31                              prune_deterministic)
    32  from .providers import Provider, ProviderError
    33  from .usage import UsageTracker
    34  from .tools import _resolve, run_tool, tool_definitions
    35  
    36  MAX_STEPS = 50
    37  MAX_PRUNE_ATTEMPTS = 5
    38  MAX_PRUNE_STEPS = 12
    39  
    40  SYSTEM_PROMPT = """You are a helpful AI assistant running inside a context-as-file harness.
    41  Answer the user directly and concisely; use tools only when the task
    42  genuinely needs them. You are on {os_name}; your working directory is
    43  {workdir}.
    44  
    45  Each turn the harness assembles your context from sources, in order:
    46  satellite files (current task, goals, facts, decisions, tasks --
    47  durable one-liners), history.md (the conversation record), and
    48  scratch.md (this episode's working notes: recent replies and tool
    49  results). There is no other memory. The assembled text below IS your context -- it is also saved
    50  to {ctx_path}, but never read that file: it duplicates what you can
    51  already see, doubling your context for nothing.
    52  
    53  CURATION (your standing permission -- edits here never need approval):
    54  you curate the durable sources with your edit tool (exact old_text /
    55  new_text, which must match exactly once):
    56    {sdir}/history.md -- the conversation record (the harness records every
    57      turn and archives old turns automatically; correct mistakes, don't
    58      manage size)
    59    {sdir}/sats/current.md -- what you are actively working on; update it
    60      when the task changes or completes
    61    {sdir}/sats/goals.md -- enduring objectives (`- [active]` / `- [done]`)
    62    {sdir}/sats/facts.md, {sdir}/sats/decisions.md, {sdir}/sats/tasks.md -- one-line facts,
    63      decisions with reasons, open tasks
    64    {sdir}/sats/hypotheses.md -- half-formed ideas, unproven suspicions (prune ruthlessly)
    65  
    66    Heavy curation (large histories, many files to triage): prefer
    67    `delegate` -- a subagent does the reading and editing in its own
    68    context; you read back only its result file. The noise never enters
    69    your transcript. This is not laziness, it is the architecture: your
    70    active context is the scarce resource, and spending a fraction of
    71    budget on a subagent to keep it clean is the win. Never apologize
    72    for delegating; apologize for bloating your own context instead.
    73  
    74  Harness-owned files cannot be touched: prompt.md, state.json,
    75  index.md, scratch.md, archive/, {ctx_path}. Write is rejected on all
    76  session sources -- use edit. Read is rejected on {ctx_path}: the
    77  assembled text is already your conversation below.
    78  
    79  EPISODES: your replies and tool results accumulate in the episode's
    80  working notes. When you reply without calling tools, the episode
    81  closes: the notes are archived, your reply is recorded in history,
    82  and old history is archived automatically the same way. Nothing you
    83  need to do -- the harness handles it. You can re-read anything
    84  archived by its path when you truly need the detail.
    85  
    86  TOOLS: Use the native function-calling tools provided by the API
    87  (read, write, edit, exec, tokens). Call them directly -- do NOT emit
    88  tool calls as text, XML, or JSON blocks in your reply.
    89  
    90  IMPORTANT: After using tools, ALWAYS reply with a text summary of what
    91  you did or found. Never end your turn with only tool calls and no text.
    92  The user needs to see your response in the chat.
    93  
    94  Never emit `## ` headers in your reply text -- the harness adds
    95  structure when it records your reply.
    96  
    97  BUDGET: hard limit {hard:,} tokens, soft warning at {soft:,} tokens.
    98  The harness shows your usage every turn. If the next request would
    99  exceed the hard limit, you do NOT get a normal turn -- you get a
   100  curation turn where you may only edit history.md and the sats until
   101  usage is back under the limit. The harness never silently truncates
   102  your context.
   103  
   104  APPROVALS: mutating tools (exec, write, edit outside your sources)
   105  need human approval: the human may approve once, approve for the session,
   106  or deny. A denied call returns a DENIED message -- respect it, do not
   107  retry the same call, work another way or ask the user.
   108  """
   109  
   110  PRUNE_SYSTEM = """You are over your context budget. This is a curation-only turn.
   111  
   112  You may ONLY use the edit tool, and ONLY on these files:
   113    {history_path} -- the conversation record
   114    {sats_dir}/current.md, {sats_dir}/goals.md, {sats_dir}/facts.md,
   115    {sats_dir}/decisions.md, {sats_dir}/tasks.md,
   116    {sats_dir}/hypotheses.md -- durable one-liners
   117  
   118  History size is harness-managed (old turns archive automatically);
   119  do not summarize it. If the sats have grown unbounded, trim them to
   120  one-liners and drop what no longer serves the task. Each edit needs
   121  old_text matching exactly once -- include enough surrounding context.
   122  Keep the `## ` section format parseable.
   123  Do not attempt the user's task now -- just curate.
   124  Reply with one line (PRUNED) only after your edits have actually shrunk
   125  the assembled context -- the harness re-measures, and an unchanged
   126  assembly just repeats this turn. Replying PRUNED without editing
   127  accomplishes nothing.
   128  """
   129  
   130  
   131  class BudgetExceeded(Exception):
   132      pass
   133  
   134  
   135  @dataclass
   136  class Session:
   137      id: str
   138      dir: Path
   139      workdir: str
   140      context: ContextFile = field(init=False)
   141  
   142      def __post_init__(self):
   143          # Blank-slate layout (T1): prompt, state, index, history, sats,
   144          # scratch, archive. Idempotent -- re-opening keeps everything.
   145          # context.md is the assembled artifact (T3/T6), not a transcript.
   146          from .session import init_layout
   147          init_layout(self.dir, SYSTEM_PROMPT)
   148          self.context = ContextFile(self.dir / "context.md")
   149  
   150  
   151  def _estimate(system: str, messages: list[dict],
   152                tools: list[dict] | None = None) -> int:
   153      total = count_tokens(system)
   154      for m in messages:
   155          total += count_tokens(m.get("content") or "")
   156          for tc in m.get("tool_calls") or []:
   157              total += count_tokens(json.dumps(tc.get("arguments") or {}))
   158      for t in tools or []:
   159          # Tool schemas ride along on every request; count them too.
   160          total += count_tokens(json.dumps(t))
   161      return total
   162  
   163  
   164  class TurnInterrupted(Exception):
   165      """Cooperative turn cancellation via Loop.request_stop().
   166  
   167      Raised at safe points (between steps, before model calls and tool
   168      executions); never escapes run_turn -- it is caught there, an
   169      "interrupted" event is emitted, and the partial turn ends.
   170      """
   171  
   172  
   173  def _parse_delegate_model(spec: str,
   174                            default_provider: str) -> tuple[str, str]:
   175      """'provider/model' or bare 'model' -> (provider, model)."""
   176      spec = spec.strip()
   177      if "/" in spec:
   178          provider, model = spec.split("/", 1)
   179          return provider.strip(), model.strip()
   180      return default_provider, spec
   181  
   182  
   183  class Loop:
   184      def __init__(self, provider: Provider, budget: Budget,
   185                   on_event=None, prune_provider: Provider | None = None,
   186                   approver: Approver | None = None,
   187                   exec_timeout: int = EXEC_TIMEOUT_DEFAULT,
   188                   exec_timeout_max: int = EXEC_TIMEOUT_MAX,
   189                   usage_tracker: UsageTracker | None = None,
   190                   usage_note: bool = True,
   191                   project: str | None = None,
   192                   prune_target: int | None = None,
   193                   prune_keep_tools: int = DEFAULT_KEEP_RECENT_TOOLS,
   194                   prune_section_cap: int = DEFAULT_SECTION_CAP,
   195                   prune_enabled: bool = True,
   196                   history_cap: int | None = None,
   197                   is_subagent: bool = False,
   198                   subagent_id: str | None = None,
   199                   tools_allowlist: list[str] | None = None,
   200                   subagent_budget_fraction: float = 0.25,
... (1089 total lines)
## assistant

```tool-calls
[{"id": "8c5nkkHzzmM7Bkrb4ukwqZU1lCR8qYD3", "name": "read", "arguments": {"path": "D:\\projects\\context-harness\\harness\\loop.py", "offset": 200, "limit": 300}}]
```
## tool 8c5nkkHzzmM7Bkrb4ukwqZU1lCR8qYD3
D:\projects\context-harness\harness\loop.py  [lines 200-499 of 1089]
   200                   subagent_budget_fraction: float = 0.25,
   201                   delegate_model: str | None = None,
   202                   provider_factory=None,
   203                   include_text: str | None = None,
   204                   include_subagents: bool = False,
   205                   user_tool_paths: list | None = None):
   206          self.provider = provider
   207          # Janitor model for prune-only turns; defaults to the main provider.
   208          self.prune_provider = prune_provider or provider
   209          self.budget = budget
   210          self.on_event = on_event or (lambda kind, data: None)
   211          self.approver = approver
   212          self.exec_timeout = exec_timeout
   213          self.exec_timeout_max = exec_timeout_max
   214          self.tracker = usage_tracker
   215          self.usage_note = usage_note
   216          self.project = project
   217          self.prune_target = prune_target  # None -> soft budget
   218          self.prune_keep_tools = prune_keep_tools
   219          self.prune_section_cap = prune_section_cap
   220          self.prune_enabled = prune_enabled
   221          # User include text: appended to system prompt under "# User includes".
   222          self.include_text = include_text
   223          self.include_subagents = include_subagents
   224          # H2: history token cap; oldest turns archive deterministically.
   225          # None -> half the soft budget.
   226          self.history_cap = (history_cap if history_cap is not None
   227                              else budget.soft // 2)
   228          self._turn_seq = 0
   229          self._stop_event = threading.Event()
   230          self.is_subagent = is_subagent
   231          self.subagent_id = subagent_id
   232          self._subagent_budget_fraction = subagent_budget_fraction
   233          self._delegate_model = delegate_model
   234          self._provider_factory = provider_factory
   235          self._tools = tool_definitions(
   236              include_delegation=not is_subagent)
   237          # User tools: scripts as tools, loaded from manifests.
   238          self._user_executors: dict = {}
   239          if user_tool_paths:
   240              from .user_tools import load_user_tools
   241              defs, execs = load_user_tools(user_tool_paths)
   242              # Don't let user tools shadow built-ins.
   243              builtin_names = {t["name"] for t in self._tools}
   244              for d in defs:
   245                  if d["name"] not in builtin_names:
   246                      self._tools.append(d)
   247              self._user_executors = {
   248                  k: v for k, v in execs.items()
   249                  if k not in builtin_names
   250              }
   251          if tools_allowlist is not None:
   252              allowed = set(tools_allowlist)
   253              self._tools = [t for t in self._tools
   254                             if t["name"] in allowed]
   255          # One subagent slot per parent loop (spec: one at a time).
   256          self._subagent: dict | None = None
   257          # Completed subagents, drained into history.md at episode close
   258          # so the model retains a durable record of what it delegated.
   259          self._completed_subagents: list[dict] = []
   260          # Circuit breaker: tool name -> consecutive denial/error
   261          # count. Stops the model spamming calls the harness keeps
   262          # refusing (keyed on tool, not args: varying the command
   263          # after a denial is still spamming).
   264          self._denial_counts: dict[str, int] = {}
   265          # Curation turns are edit-only: write is denied on all
   266          # session sources by the gates (T5), so offering it would only
   267          # produce DENIED noise.
   268          self._prune_tools = [t for t in self._tools
   269                               if t["name"] in ("edit",)]
   270  
   271      def request_stop(self) -> None:
   272          """Ask a running turn to stop at the next safe point.
   273  
   274          Cooperative: a turn blocked inside a model call or tool
   275          execution finishes that call first (bounded by the request /
   276          exec timeouts), then aborts before storing or acting on the
   277          result. A completed turn always consumes the flag, so a stop
   278          can never leak into and kill the next turn.
   279          """
   280          self._stop_event.set()
   281  
   282      def _check_stop(self) -> None:
   283          if self._stop_event.is_set():
   284              raise TurnInterrupted()
   285  
   286      def _emit(self, kind: str, data):
   287          self.on_event(kind, data)
   288  
   289      def _measure(self, provider: Provider, system: str,
   290                     messages: list[dict], tools: list[dict]) -> dict:
   291          """Break the next request into measurable parts.
   292  
   293          system: harness instructions. transcript: the wire-format messages
   294          (translated when the provider offers it, so JSON envelope keys
   295          count). tools: the schemas riding along. total drives the meter
   296          and the budget; the wire template on the server side remains a
   297          small unmeasured margin.
   298          """
   299          translate = getattr(provider, "translate_messages", None)
   300          wire = translate(messages) if callable(translate) else messages
   301          parts = {"system": count_tokens(system),
   302                   "transcript": count_tokens(json.dumps(wire)),
   303                   "tools": sum(count_tokens(json.dumps(t)) for t in tools)}
   304          parts["total"] = parts["system"] + parts["transcript"] + parts["tools"]
   305          return parts
   306  
   307      def _track(self, phase: str, step: int, breakdown: dict,
   308                 server: dict | None) -> dict:
   309          """Record one model call; returns running session totals."""
   310          if self.tracker is None:
   311              return {"input": 0, "output": 0, "estimated": 0}
   312          return self.tracker.record(turn=self._turn_seq, phase=phase,
   313                                     step=step, breakdown=breakdown,
   314                                     server=server)
   315  
   316      def _note(self, breakdown: dict) -> list[dict]:
   317          """Ephemeral usage line: sent to the model, never stored.
   318  
   319          The meter is console-only, so without this the model reasons
   320          from stale numbers pasted into its transcript. ~30 tokens.
   321          The project line rides along when a project is active.
   322          """
   323          if not self.usage_note and not self.project:
   324              return []
   325          t = self.tracker.totals if self.tracker else {"input": 0,
   326                                                        "output": 0}
   327          lines = []
   328          if self.project:
   329              lines.append(f"[project: {self.project}]")
   330          if self.usage_note:
   331              lines.append(
   332                  f"[harness note: this request ≈ {breakdown['total']:,} "
   333                  f"tokens (sys {breakdown['system']:,} + "
   334                  f"chat {breakdown['transcript']:,} + "
   335                  f"tools {breakdown['tools']:,}) of "
   336                  f"{self.budget.hard:,} budget -- only this-request tokens "
   337                  f"count against budget. Session lifetime in {t['input']:,} "
   338                  f"out {t['output']:,} is informational, not budget.]")
   339          return [{"role": "user", "content": "\n".join(lines)}]
   340  
   341      def _source_target(self, session: Session, name: str,
   342                           args: dict) -> Path | None:
   343          """Editable file for backup+diff, else None (T5).
   344  
   345          history/sat sources get pre-edit snapshots and mechanical
   346          diffs.
   347          """
   348          if name not in ("write", "edit"):
   349              return None
   350          try:
   351              from .tools import classify_source
   352              p = _resolve(args.get("path") or "", session.workdir)
   353              if classify_source(session.dir, p) in ("history", "sat"):
   354                  return p
   355          except Exception:
   356              pass
   357          return None
   358  
   359      def _policy(self, session: Session) -> Policy:
   360          return Policy(session.workdir, session.dir, session.context.path)
   361  
   362      def _naive_truncate(self, session: Session) -> None:
   363          """Condition A: drop oldest sections until under hard budget.
   364  
   365          No dedupe, no archive, no model call. The 'stupid and lazy'
   366          baseline for A/B evaluation.
   367          """
   368          from .deterministic import _split_sections, _split_preamble, _render
   369          from .context import count_tokens
   370          try:
   371              text = session.context.load()
   372              pre, _ = _split_preamble(text)
   373              sections = _split_sections(text)
   374              # Drop from oldest until under hard. Keep at least 2 sections.
   375              while len(sections) > 2:
   376                  if count_tokens(_render(sections, pre)) < self.budget.hard:
   377                      break
   378                  sections.pop(0)
   379              session.context.save(_render(sections, pre))
   380              self._emit("naive-truncate", {"sections_kept": len(sections)})
   381          except Exception:
   382              pass
   383  
   384      def _auto_dedupe(self, session: Session) -> None:
   385          """Run exact-dedupe on scratch.md after every tool result (T6).
   386  
   387          The harness does this mechanically -- byte-identical assistant/tool
   388          sections collapse to the newest. No model call, no budget check.
   389          Skipped in naive mode (--no-prune).
   390          """
   391          if not self.prune_enabled:
   392              return
   393          from .deterministic import dedupe_exact
   394          try:
   395              p = session.dir / "scratch.md"
   396              before = p.read_text(encoding="utf-8", errors="replace")
   397              after, dropped = dedupe_exact(before)
   398              if dropped and after != before:
   399                  p.write_text(after, encoding="utf-8")
   400                  self._emit("auto-dedupe", {"dropped": dropped})
   401          except Exception:
   402              pass
   403  
   404      @staticmethod
   405      def _append_source(session: Session, name: str, text: str) -> None:
   406          """Append to a session source file (history.md, scratch.md)."""
   407          p = session.dir / name
   408          cur = p.read_text(encoding="utf-8", errors="replace") \
   409              if p.exists() else ""
   410          if cur and not cur.endswith("\n"):
   411              cur += "\n"
   412          p.write_text(cur + text, encoding="utf-8")
   413  
   414      def _backup_context(self, session: Session, target: Path,
   415                          content: str, label: str):
   416          """Snapshot pre-edit source content (best effort).
   417  
   418          Prune turns label theirs `pre-prune` (historical name kept);
   419          ordinary model curation labels `pre-edit`. The backup carries
   420          the source file's stem. Collision-safe: same-second edits
   421          append a counter. Returns the path or None.
   422          """
   423          import datetime as _dt
   424          try:
   425              stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
   426              base = target.with_name(
   427                  f"{target.stem}.pre-{label}-{stamp}.bak")
   428              backup = base
   429              n = 1
   430              while backup.exists():
   431                  backup = base.with_name(f"{base.stem}-{n}.bak")
   432                  n += 1
   433              backup.write_text(content, encoding="utf-8")
   434              self._emit("backup", {"path": str(backup), "label": label})
   435              return backup
   436          except OSError:
   437              return None  # best effort: never block a turn on a backup
   438  
   439      def _close_episode(self, session: Session, turn_no: int,
   440                         reply: str = "") -> None:
   441          """Archive scratch.md, clear it, record the pointer (T7).
   442  
   443          Runs when the assistant replies with no more tool calls. The
   444          full episode trace moves to archive/<date>-t<NNNN>.md; history
   445          keeps a cheap `## episode t<NNNN>` pointer (archive path, turn
   446          range, tool-call count). H1: the closing reply is recorded to
   447          history deterministically (stamped) -- the model is never asked
   448          to decide what is durable. Then history is windowed (H2).
   449          Best effort: never block a turn.
   450          """
   451          import datetime as _dt
   452          import re as _re
   453          try:
   454              scratch_p = session.dir / "scratch.md"
   455              content = (scratch_p.read_text(encoding="utf-8",
   456                                             errors="replace")
   457                         if scratch_p.exists() else "")
   458              tool_calls = len(_re.findall(r"(?m)^## tool \S+", content))
   459              archive_dir = session.dir / "archive"
   460              archive_dir.mkdir(parents=True, exist_ok=True)
   461              stamp = _dt.datetime.now().strftime("%Y%m%d")
   462              dest = archive_dir / f"{stamp}-t{turn_no:04d}.md"
   463              n = 2
   464              while dest.exists():
   465                  dest = archive_dir / f"{stamp}-t{turn_no:04d}-{n}.md"
   466                  n += 1
   467              dest.write_text(content, encoding="utf-8")
   468              scratch_p.write_text("", encoding="utf-8")
   469              tag = f"t{turn_no:04d}"
   470              pointer = (f"## episode {tag}\n"
   471                         f"archive: archive/{dest.name}\n"
   472                         f"turns: {tag}-{tag}\n"
   473                         f"tool_calls: {tool_calls}\n")
   474              # Durable subagent record: the model must remember what it
   475              # delegated, or it concludes it fabricated the results.
   476              if self._completed_subagents:
   477                  lines = ["subagents:"]
   478                  for c in self._completed_subagents:
   479                      lines.append(
   480                          f"  - {c['id']}: \"{c['task']}\" [{c['model']}] -> "
   481                          f"{c['status']} ({c['result_path']})")
   482                  pointer += "\n".join(lines) + "\n"
   483                  self._completed_subagents.clear()
   484              self._append_source(session, "history.md", pointer)
   485              # H1: the closing reply joins history, stamped. Empty
   486              # replies record nothing.
   487              if reply.strip():
   488                  self._append_source(
   489                      session, "history.md",
   490                      f"## assistant {tag}\n{reply.strip()}\n")
   491              # H2: deterministic history windowing.
   492              self._window_history(session)
   493              # Refresh the inspectable artifact so it reflects the
   494              # closed episode, not the pre-close assembly.
   495              write_assembled(session.dir, assemble(session.dir))
   496              self._emit("episode-close", {"turn": turn_no,
   497                                           "archive": f"archive/{dest.name}",
   498                                           "tool_calls": tool_calls})
   499          except OSError:
... (1089 total lines)
