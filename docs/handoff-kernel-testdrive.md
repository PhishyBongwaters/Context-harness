# Handoff test drive: Linux kernel write-path trace

Live acceptance run for the handoff-per-turn feature (issue #5).
Successor to the simulated evaluation in `docs/handoff-prompt-eval.md`.

## The question

> In this kernel tree, trace a buffered `write()` syscall on an ext4
> file from syscall entry to block-layer submission. At each layer,
> name the function and the file it lives in. Then list every point
> where the write can block (sleep), and what it sleeps on.
>
> Scope: buffered I/O only — not direct I/O, not io_uring.

## Why this question

- **Long horizon.** The trace crosses four or more subsystems (arch
  entry, VFS, ext4, page cache, block layer). No single turn finishes
  it; the agent must carry "next" across many turns.
- **Forced choices.** The tree also contains the direct-I/O and
  io_uring paths. The agent has to notice them, rule them out, and
  record the ruling as a one-line dead end in Next — the exact
  behavior Phase 1 was built for.
- **Version dependence.** Function names and layering differ between
  kernel versions, so the agent cannot answer from training data; it
  must read this tree. Drift is detectable against the known path.
- **Bloat pressure.** A trace tempts dumping (function bodies, call
  graphs) into the handoff. That is what the canary
  (`handoff-bloat`) and the deterministic trim backstop exist to
  catch.

## Setup

- Kernel tree at a pinned version, e.g.
  `git clone --depth 1 --branch v6.12 https://github.com/torvalds/linux`
- Harness session rooted at the tree. Read-only is fine; the agent
  only needs to read.
- Live model (Rob's rig).

## What to observe (the handoff under test)

1. **Resume fidelity.** After several turns, end the session and start
   a fresh turn. Does the handoff's Next say exactly where to
   continue — or does the agent re-derive from scratch?
2. **Dead ends.** Is the direct-I/O path recorded as a one-line dead
   end in Next, ruled out with its reason?
3. **Pointers.** Do the recorded file/function references resolve to
   real locations in this tree?
4. **Curation vs dumping.** Does `sats/handoff.json` hold steady in
   size (curating) or climb (dumping)? Did `handoff-bloat` fire?
5. **Forced rounds / trim.** Did a stale handoff trigger a forced
   round? Did the deterministic trim ever engage, and was the trim
   marker present?
6. **Answer correctness.** The final trace against the known shape of
   the path (syscall entry → `sys_write`/`ksys_write` → `vfs_write` →
   `ext4_file_write_iter` → `generic_perform_write` → … → block-layer
   submission; exact names vary by version).

## Pass bar

The handoff carries Now *and* Next across every turn boundary; dead
ends, blockers, and pointers survive where the old compaction lost
them; the agent reaches the right answer without re-tracing ground it
already covered.
