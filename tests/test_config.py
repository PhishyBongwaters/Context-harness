import unittest
from unittest import mock

from harness.config import _windows_base_dirs, load_config


class TestPlatformPaths(unittest.TestCase):
    def test_windows_env_dirs(self):
        env = {"APPDATA": "C:\\Users\\Rob\\AppData\\Roaming",
               "LOCALAPPDATA": "C:\\Users\\Rob\\AppData\\Local"}
        with mock.patch.dict("os.environ", env, clear=False):
            appdata, local = _windows_base_dirs()
            self.assertEqual(appdata, env["APPDATA"])
            self.assertEqual(local, env["LOCALAPPDATA"])

    def test_windows_env_fallback_to_home(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            appdata, local = _windows_base_dirs()
            # no APPDATA/LOCALAPPDATA -> falls back to home, and both agree
            self.assertEqual(appdata, local)
            self.assertTrue(appdata)

    def test_load_config_applies_default_sessions_dir(self):
        cfg = load_config(path="/nonexistent/path.json")
        self.assertTrue(cfg.sessions_dir)

    def test_explicit_sessions_dir_wins(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"sessions_dir": "/custom/dir"}))
            cfg = load_config(path=p)
            self.assertEqual(cfg.sessions_dir, "/custom/dir")


if __name__ == "__main__":
    unittest.main()
