# Goals

Enduring objectives, one per line: `- [active] ...` / `- [done] ...`.
- [done] In this kernel tree, trace a buffered `write()` syscall on an ext4 file from syscall entry to block-layer submission. At each layer,name the  function and the file it lives in. Then list every point where the write can block (sleep), and what it sleeps on. Scope: buffered I/O only — not direct I/O, not io_uring.
