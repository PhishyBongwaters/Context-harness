# Handoff-per-turn: prompt evaluation (issue #5, Phase 1)

Date: 2026-10-15. Evaluator: Data, acting as the test-subject agent.
Question under test: **would this prompting steer an agent correctly?**

Method: (1) adversarial cold read of the exact rendered prompt;
(2) three simulated turns — write the handoff the prompt asks for,
then judge from the *next* turn's perspective whether resumption
works from the handoff alone; (3) lazy-model stress: enumerate gaming
strategies and check each against the harness mechanism.
No live model was available in this runtime, so the behavioral half is
simulated and the mechanical half is unit-tested (tests/test_handoff.py).

## 1. What the agent actually sees

`load_prompt(..., turn_no=42)` renders (abridged to the handoff block):

```
HANDOFF (every turn ends with one -- this is how you survive the night):
  {sdir}/sats/handoff.md -- your shift handoff to your next self. There
    is no other memory: pruned and archived turns leave your context,
    and anything not written into the sats or history is gone. Assume
    each turn is a handoff -- the next turn wakes up knowing ONLY what
    is in the assembled sources.
  Rewrite the whole handoff at the end of every turn, before you finish:
    ## Handoff -- t0042
    ### Now -- where things stand (2-4 lines, not a biography)
    ### Next -- the intended next steps, in order; the hypothesis in
      flight and what would prove or disprove it; dead ends already
      tried, one line each
    ### Blockers -- what is stuck, and what would unstick it
    ### Pointers -- where the state lives (file paths, sat sections),
      never copies of the state itself
  Rules: rewrite, never append. Under 40 lines -- it is a shift board,
  not a biography. "Unchanged -- still <reason>" is a legal handoff,
  but the stamp must read t0042. The harness checks the handoff
  before your turn ends; a stale or missing handoff sends you back for
  one more round.
```

The forced-round nudge (on validation failure) names the exact file,
the exact expected header, and the validator's reason.

## 2. Adversarial read

- **Stamp is unambiguous.** The literal `## Handoff -- t0042` leaves no
  room for interpretation. Near-misses (en dash, `#` vs `##`) fail
  validation and the nudge repeats the exact header — self-correcting
  within one forced round.
- **"Rewrite" vs the edit tool.** The prompt says "rewrite the whole
  handoff" but the edit tool needs exact old/new matches. In practice
  any edit sequence landing a valid file passes — the prompt states
  intent, the harness verifies outcome. The rule that matters
  ("rewrite, never append") is what the cap enforces.
- **"Unchanged -- still \<reason\>" is the pressure valve.** It keeps
  the mechanism honest on blocked turns instead of forcing fiction.
  Risk: an agent coasts on "Unchanged" while Next goes stale. Bound:
  the handoff's reader is the agent itself — stale Next hurts its own
  next turn, which is the correct incentive alignment.
- **Pointers > copies** is the anti-dump rule and the most important
  line in the block. It directly counters the known failure mode
  (dumb models dumping everything into sats/current.md).
- **Dead ends** had no named home in the first draft; added to Next
  after scenario C below. One-line dead ends are the highest-value
  anti-repeat context per token.

## 3. Simulated turns

**A. Mid-task progress (kernel bisect).** Handoff records: bisection
result + repro path (Now), lockstat hypothesis + cpuidle dead end
(Next), root-access ticket (Blockers), facts.md + repro + archived
episode (Pointers). Next-turn verdict: full resume from the handoff
alone. The hypothesis-in-flight — the thing compaction murders first —
survives. PASS.

**B. Blocked on the user (SSO .env values).** "Unchanged -- still
waiting on Rob" with a concrete resume plan in Next. Next-turn
verdict: perfect resume; the "Unchanged" is informative, not lazy.
PASS.

**C. Dead-end-heavy turn.** Three approaches tried, all failed. The
prompt's Next slot ("dead ends already tried, one line each") +
Pointers ("never copies") steers to one-liners, not a dump. Weakest
of the three — a minimal model could still write "tried some things,
didn't work" — but the 40-line cap and "not a biography" push back.
PASS with noted residual.

## 4. Lazy-model stress

| Strategy | Mechanism response |
|---|---|
| Stamp-touch only ("Unchanged" forever) | Legal; self-limiting (hurts own next turn) |
| Empty sections (headings, no content) | Passes today — documented residual; content quality is the model's job |
| Wrong file (handoff in scratch.md) | Validator reads sats/handoff.md specifically; nudge names the path |
| Deletes handoff.md via exec | `ensure_handoff_file` restores the template before the forced round |
| 200-line dump | 40-line cap → forced round: "trim it to a shift board" |
| Ignores the nudge / does other work | Forced round is edit-only; 2 failed attempts → `handoff-invalid` event, turn still closes (loud, never blocking) |
| Future stamp (t0043 during t0042) | Exact-match validation rejects it; can't bank a stamp |
| Double header / junk after valid block | Passes shape check — residual; bounded by the cap and next-turn readability |

## 5. Verdict

**Yes, it steers correctly** — and the mechanism, not the prose, does
the heavy lifting. The prompt leaves zero ambiguity about the expected
artifact (literal stamp, four named sections, 40-line cap); the four
sections map exactly to what long-horizon turns lose (state, intent,
blockers, pointers); and the enforcement makes compliance the cheapest
path: forget → nudge with the exact reason → edit-only round → loud
failure that never wedges the session.

Residuals for later phases: empty-section gaming, multi-turn
"Unchanged" staleness, pathological double-headers. All bounded by the
cap and by the fact that the handoff's victim is the agent itself.
Phase 2 (caps/canary/prune integration) can tighten these if the
kernel-scale acceptance run shows they matter in practice.
