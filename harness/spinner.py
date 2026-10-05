"""Console activity indicator for blocking model calls.

One daemon thread rewrites a single line (braille spinner + label +
elapsed) until stop() clears it. All output stays on one line; the next
print starts fresh. Disabled when stdout is not a tty (pipes, tests).
"""
from __future__ import annotations

import sys
import threading
import time

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(self, label: str = "thinking",
                 out=None, enabled: bool | None = None):
        self.label = label
        self.out = out or sys.stdout
        self.enabled = (self.out.isatty() if enabled is None
                        else enabled)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True,
                                        name="spinner")
        self._thread.start()

    def _spin(self) -> None:
        t0 = time.monotonic()
        i = 0
        while not self._stop.wait(0.2):
            el = time.monotonic() - t0
            try:
                self.out.write(f"\r{_FRAMES[i % len(_FRAMES)]} "
                               f"{self.label} {el:.0f}s")
                self.out.flush()
            except (OSError, ValueError):
                return
            i += 1

    def stop(self) -> None:
        t, self._thread = self._thread, None
        if t is None:
            return
        self._stop.set()
        t.join(timeout=2)
        if self.enabled:
            try:
                self.out.write("\r" + " " * 40 + "\r")
                self.out.flush()
            except (OSError, ValueError):
                pass
