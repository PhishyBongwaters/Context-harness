# TUI Proposal — separate Textual interface, CLI unchanged

Status: **draft** (`feature/tui-proposal` branch — merge nothing to `main` until V1 scope is agreed).
Track as feature request: paste this file body into a GitHub issue titled `TUI: Textual interface (CLI unchanged)`, or keep iterating here.

## 1. Goal

Add an optional terminal UI as a **second interface** to the same headless agent loop:

```text
python -m harness "task"        # CLI — default, unchanged, stdlib-only (+tiktoken)
python -m harness --tui "task"  # TUI — opt-in, requires extra
```

Non-goals for V1: streaming tokens, mouse-driven editing, GUI/web, changing `Loop` semantics, adding hard deps to the CLI path.

## 2. Why now

The core already supports a TUI without refactoring the agent:

- `harness/loop.py:127` — `Loop(provider, budget, on_event, ...)` never touches stdin/stdout directly; all output goes through `on_event(kind, data)`.
- `harness/__main__.py:79` — `_print_event` is one subscriber; `harness/debug.py:43` `DebugLog.handler` proves the tee pattern. TUI is a third subscriber.
- `harness/approvals.py:344` — `approval-wait` / `approval-result` already fire with reason + timeout. That's the modal hook.
- `request.breakdown + usage_total` (`harness/loop.py:400`) already carry everything a budget bar needs; `UsageTracker` is the shared ledger so CLI and TUI read the same numbers.

## 3. Decision: Textual as an optional extra

| Option | Verdict |
|---|---|
| A. Textual | **Pick for full TUI.** Cross-platform incl. Windows (kills `curses`), panes/modals/scroll/mouse for free. Cost: ~10 transitive deps. |
| B. Rich-live | Fallback if we want "nicer CLI" this week. Keeps REPL, no real panes/modals. |
| C. stdlib ANSI | Reject — hand-rolled input + scrollback + Windows quirks, most work for least widget. |

Packaging:

- Core stays `requirements.txt` = `tiktoken` only. `tests/` stays stdlib-only.
- TUI deps live in `requirements-tui.txt` (or `pyproject [project.optional-dependencies] tui`) — e.g. `textual>=3`.
- Import guard: `python -m harness --tui` without the extra prints install hint and exits 2. No `import textual` at module top-level outside `harness/tui/`.

```
harness/tui/
  __init__.py    # has_tui(), import guard
  app.py         # Textual App: layout, keybindings, command palette
  bridge.py      # event-queue subscriber + worker thread (see §5)
  approvals.py   # TUIApprover: modal future instead of StdinPump
  widgets.py     # transcript, budget bar, status, input (Phase 2+)
```

Single-file `harness/tui.py` is acceptable only if we downscope to Rich; with Textual go package from the start.

## 4. V1 scope (proposed)

In scope:

- Transcript pane (assistant text, `$ tool brief`, `context-diff`, `budget warn/over`, `prune` lines) — read from existing events, no new event kinds.
- Input box + status line (`thinking/pruning Ns` elapsed, replaces `harness/spinner.py` in TUI mode).
- Budget bar: `sys + chat + tools / hard (%)` from `request.breakdown`, plus `sess in/out` from `usage_total`.
- Approval modal: `(a)pprove turn / (s)ession / (d)eny` + countdown from `approval_timeout`. Default-deny on timeout, same as CLI.
- Slash parity in the input box: `/new /open /list /project /usage /config /providers /models /help /quit` — same handlers as REPL, so muscle memory transfers.

Out of scope (defer): token streaming (needs SSE in `providers.py`), multi-session splits, debug-log viewer (toggle later), mouse editing, notifications.

## 5. Architecture

```
                  ┌────────────────── Loop (worker thread) ──────────────────┐
                  │ run_turn() → provider.chat() (blocking)                 │
                  │   │ on_event(kind, data) ──thread-safe──▶ queue.Queue   │
                  │   │ Approver.resolve() ──blocks──▶ UI future (modal)     │
                  └──────────────────────────────────────────────────────────┘
                                        │ poll / call_from_thread
                  ┌────────────────── Textual App (UI thread) ───────────────┐
                  │ transcript · budget bar · status · input · approval modal│
                  └──────────────────────────────────────────────────────────┘
```

Rules:

1. **Never call `Loop` on the UI thread.** `bridge.py` owns one worker thread per turn; `on_event` only does `queue.put` + `app.call_from_thread(refresh)`.
2. **Never touch `StdinPump` in TUI mode** (`harness/approvals.py:176`). `TUIApprover` overrides `_read_answer`/`_prompt` to await a modal `asyncio.Future` with `approval_timeout`; timeout/EOF → `deny`. No stdin reads, no zombie `input()` threads.
3. **`Spinner` is CLI-only.** TUI status line derives from `request` → (`response`|`tool`|`error`) event span + wall clock.
4. **No new `Loop` semantics.** If a new event kind is needed, add it as additive optional (e.g. `turn-start`), never change existing payload shapes — CLI + `debug.jsonl` consumers must keep passing.

Approval sequence:

```text
Loop._execute_tool → Approver.resolve → ASK + no remembered key
  → emit approval-wait → TUIApprover opens modal, awaits future (timeout)
  → user a/s/d or timeout → emit approval-result → allow turn/session or DENIED
```

`--yes` still works in TUI (auto-approve path, denylist still denied) for demos.

## 6. CLI wiring

- `python -m harness --tui [--yes] [task] [--project ...]` — same session/project/usage plumbing (`_open_session`, `stamp`, `attach`), only the renderer + approver differ.
- `--debug` keeps writing `debug.jsonl` alongside; TUI may add a toggle to tail it (Phase 3).
- REPL stays exactly as-is when `--tui` is absent. No flag renames.

## 7. Phases

- **Phase 0 (this branch):** agree this doc. No code on `main`.
- **Phase 1:** `bridge.py` + read-only transcript + input + `--tui` flag + import guard + 1 smoke test (`MockProvider`, stdlib-only bridge test; Textual pilot only if installed).
- **Phase 2:** `TUIApprover` modal + budget bar + status line; `--yes` parity; manual Windows + POSIX checklist.
- **Phase 3:** session/project switching, `/usage` pane, config/providers/models views, debug tail toggle.
- **Phase 4 (later):** streaming, notifications hooking `approval-wait`.

## 8. Testing

- `python -m unittest discover -s tests` must pass with and without the TUI extra installed.
- Bridge test (stdlib): fake `on_event` → queue → assert transcript lines + budget math, using `MockProvider` script + `Approver(input_fn=...)`.
- Manual: local model (llama.cpp/LM Studio) long prefill → status shows elapsed; mutating `exec` → modal appears, timeout denies; `/usage` numbers match CLI.

## 9. Risks

- Blocking `provider.chat` stalls the worker, not the UI — but cancel (Ctrl-C) during a call needs a defined behavior (detach vs. wait; propose wait + `[interrupted]` like CLI `do_turn`).
- Textual event-loop + worker-thread writes: all DOM updates via `call_from_thread`, never directly from `on_event`.
- Windows console: verify UTF-8/braille fallback (`sys.stdout.reconfigure` pattern in `__main__.py:351` doesn't apply; Textual owns rendering).

## 10. Acceptance criteria for V1 (merge to main)

- [ ] `--tui` runs a full task on a local + a cloud provider.
- [ ] Approval modal approves/denies/timeouts correctly; denial returns `DENIED` and the model respects it.
- [ ] Budget bar matches CLI `[context …]` numbers on the same session.
- [ ] CLI output byte-identical with/without the extra installed (no Textual import on CLI path).
- [ ] `unittest discover` green, no new hard dep in `requirements.txt`.

## 11. Open questions

1. Confirm Textual (not Rich-only) as the extra?
2. V1 must include the approval modal, or is `--yes`-only V1 acceptable as a stepping stone?
3. Package as `requirements-tui.txt` now, or move to `pyproject` extras immediately?

---

## 12. Phase-2 manual checklist (run with `pip install -r requirements-tui.txt`)

All green in CI-equivalent: `python -m unittest discover -s tests`
(157 tests, 1 skipped without the extra).

- [ ] Local task: `python -m harness --tui "summarize context.md"`
  (llama.cpp/LM Studio long prefill) → status shows `thinking Ns`
  ticking, then clears; transcript shows assistant text exactly once.
- [ ] Cloud task: same on an OpenAI-compatible cloud model → same.
- [ ] Mutating exec (e.g. ask model to `git push` or write outside the
  transcript) → modal shows tool + brief + reason + `default deny in Ns`.
  `a` approves the turn (model proceeds), `s` approves + writes
  `approvals.json` (no re-prompt this session), `d`/esc denies
  (tool line shows `[denied]`, model gets DENIED).
- [ ] Modal timeout: leave the modal open past `approval_timeout`
  (try `--approval-timeout 10`) → denies by itself, modal closes.
- [ ] Budget bar vs CLI: run the same session in CLI and TUI; the TUI
  budget line numbers (`ctx / hard (%)`, `sys/chat/tools`, `sess in/out`)
  match the CLI `[context ...]` line; `warn` = yellow, `over` = red.
- [ ] `--yes` parity: `python -m harness --tui --yes "task"` never opens
  the modal (auto-approves askable tools); denylist (e.g. `rm -rf /`)
  is still denied.
- [ ] Ctrl-C mid-call: record observed behaviour (expected: TUI exits,
  daemon worker dies, session file persists; resume with same session).
  Observed: _______________________________________________.

*Next action: Phase 3 (session/project switching, /usage pane,
config/providers/models views, debug tail toggle).*
