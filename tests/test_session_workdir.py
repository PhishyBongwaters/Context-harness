"""Session workdir must survive resume.

Opening a session without an explicit --workdir restores the workdir
recorded at creation instead of silently adopting the launcher's cwd.

Regression: session 20261021-163926 was created with workdir
D:\\Projects\\Tricorder-Testing-Repos\\linux but resumed from
D:\\Projects\\Context-harness without --workdir, so the model was told
the harness dir was its project, listed it, found no kernel tree, and
answered the trace from memory.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.__main__ import _new_session, _open_session


def _cfg(tmp):
    return SimpleNamespace(sessions_path=Path(tmp))


class TestSessionWorkdirPersistence(unittest.TestCase):
    def test_new_session_stores_workdir(self):
        with tempfile.TemporaryDirectory() as d:
            s = _new_session(_cfg(d), "/tmp/projA")
            meta = json.loads((s.dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["workdir"], "/tmp/projA")
            self.assertEqual(s.workdir, "/tmp/projA")

    def test_reopen_restores_stored_workdir(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _cfg(d)
            s1 = _new_session(cfg, "/tmp/projA")
            s2 = _open_session(cfg, s1.id, "/tmp/elsewhere",
                               workdir_explicit=False)
            self.assertEqual(s2.workdir, "/tmp/projA")
            meta = json.loads((s1.dir / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["workdir"], "/tmp/projA")

    def test_explicit_workdir_overrides_and_updates(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _cfg(d)
            s1 = _new_session(cfg, "/tmp/projA")
            s2 = _open_session(cfg, s1.id, "/tmp/projC",
                               workdir_explicit=True)
            self.assertEqual(s2.workdir, "/tmp/projC")
            s3 = _open_session(cfg, s1.id, "/tmp/elsewhere",
                               workdir_explicit=False)
            self.assertEqual(s3.workdir, "/tmp/projC")

    def test_old_session_without_meta_uses_passed_workdir(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = _cfg(d)
            s1 = _new_session(cfg, "/tmp/projA")
            (s1.dir / "meta.json").unlink()
            s2 = _open_session(cfg, s1.id, "/tmp/elsewhere",
                               workdir_explicit=False)
            self.assertEqual(s2.workdir, "/tmp/elsewhere")


if __name__ == "__main__":
    unittest.main()
