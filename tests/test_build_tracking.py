"""Build tracking: a new session records its commit in build.json.

A session folder must prove which harness commit produced it, without
trusting the live BUILD pill. Missing build.json means pre-tracking;
a re-opened session is never backfilled with a newer build.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.session import (export_transcript, harness_build_info,
                             init_layout)


class TestBuildTracking(unittest.TestCase):
    def test_new_session_writes_build_json(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = init_layout(sdir, prompt_text="P")
            self.assertIn("build", paths)
            build_path = paths["build"]
            self.assertTrue(build_path.is_file())
            info = json.loads(build_path.read_text(encoding="utf-8"))
            for key in ("commit", "short", "branch", "dirty",
                        "recorded_at"):
                self.assertIn(key, info, key)
            if info["commit"] != "unknown":
                self.assertEqual(len(info["commit"]), 40)
                self.assertEqual(info["short"], info["commit"][:7])
            self.assertIsInstance(info["branch"], str)
            self.assertTrue(info["dirty"] in (True, False, None))

    def test_reopen_does_not_overwrite_build_json(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            init_layout(sdir, prompt_text="P")
            sentinel = {"commit": "sentinel", "short": "sentine",
                        "branch": "sentinel", "dirty": False,
                        "recorded_at": "sentinel"}
            (sdir / "build.json").write_text(json.dumps(sentinel),
                                             encoding="utf-8")
            paths2 = init_layout(sdir, prompt_text="P")
            self.assertIn("build", paths2)
            info = json.loads(paths2["build"].read_text(
                encoding="utf-8"))
            self.assertEqual(info["commit"], "sentinel")

    def test_reopen_without_build_json_does_not_backfill(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            init_layout(sdir, prompt_text="P")
            (sdir / "build.json").unlink()
            paths2 = init_layout(sdir, prompt_text="P")
            self.assertNotIn("build", paths2)
            self.assertFalse((sdir / "build.json").exists())

    def test_harness_build_info_never_raises_on_git_failure(self):
        with mock.patch("harness.session.subprocess.run",
                        side_effect=RuntimeError("git down")):
            info = harness_build_info()
        self.assertEqual(info["commit"], "unknown")
        self.assertEqual(info["short"], "unknown")
        self.assertEqual(info["branch"], "unknown")
        self.assertIsNone(info["dirty"])

    def test_export_includes_build_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            init_layout(sdir, prompt_text="P")
            out = export_transcript(sdir)
            self.assertIn("Build:", out)

    def test_export_without_build_json_does_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            init_layout(sdir, prompt_text="P")
            (sdir / "build.json").unlink()
            out = export_transcript(sdir)
            self.assertNotIn("Build:", out)


if __name__ == "__main__":
    unittest.main()
