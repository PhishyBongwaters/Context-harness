"""Windows conhost shows raw \\x1b[... sequences as garbage text unless
ENABLE_VIRTUAL_TERMINAL_PROCESSING is set. _enable_windows_ansi() must
flip that flag on Windows and never break startup anywhere."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.__main__ import _enable_windows_ansi


def _fake_ctypes(calls, mode_value=0x0003, get_ok=True):
    class FakeKernel32:
        def GetStdHandle(self, n):
            calls["std_handle"] = n
            return 99

        def GetConsoleMode(self, handle, mode_ref):
            calls["get_mode"] = handle
            mode_ref._obj.value = mode_value
            return 1 if get_ok else 0

        def SetConsoleMode(self, handle, mode):
            calls["set_mode"] = (handle, mode)
            return 1

    return SimpleNamespace(
        windll=SimpleNamespace(kernel32=FakeKernel32()),
        c_ulong=lambda value=0: SimpleNamespace(value=value),
        byref=lambda obj: SimpleNamespace(_obj=obj),
    )


class TestWindowsAnsi(unittest.TestCase):
    def test_noop_off_windows(self):
        calls = []

        def fail_system(cmd):  # pragma: no cover
            calls.append(cmd)

        with patch.object(os, "name", "posix"):
            with patch.dict(sys.modules, {"ctypes": None}):
                with patch.object(os, "system", fail_system):
                    _enable_windows_ansi()  # must not raise, must not touch console
        self.assertEqual(calls, [])

    def test_enables_vt_processing_on_windows(self):
        calls = {}
        with patch.object(os, "name", "nt"):
            with patch.dict(sys.modules,
                            {"ctypes": _fake_ctypes(calls)}):
                _enable_windows_ansi()
        self.assertEqual(calls["std_handle"], -11)  # STD_OUTPUT_HANDLE
        self.assertEqual(calls["get_mode"], 99)
        self.assertEqual(calls["set_mode"], (99, 0x0003 | 0x0004))

    def test_skips_set_when_vt_already_on(self):
        calls = {}
        with patch.object(os, "name", "nt"):
            with patch.dict(sys.modules,
                            {"ctypes": _fake_ctypes(calls,
                                                    mode_value=0x0007)}):
                _enable_windows_ansi()
        self.assertIn("get_mode", calls)
        self.assertNotIn("set_mode", calls)

    def test_console_failures_are_silent(self):
        calls = {}
        with patch.object(os, "name", "nt"):
            with patch.dict(sys.modules,
                            {"ctypes": _fake_ctypes(calls, get_ok=False)}):
                _enable_windows_ansi()  # GetConsoleMode failed: no crash
        self.assertNotIn("set_mode", calls)

        with patch.object(os, "name", "nt"):
            with patch.dict(sys.modules, {"ctypes": None}):
                _enable_windows_ansi()  # ctypes broken: no crash


if __name__ == "__main__":
    unittest.main()
