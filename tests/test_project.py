import os
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import Loop, Session
from harness.project import (load_registry, resolve_project, save_registry,
                             session_project, set_session_project)
from harness.providers import MockProvider


class TestRegistry(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "projects.json"
            save_registry({"a": {"workdir": d}}, p)
            self.assertEqual(load_registry(p), {"a": {"workdir": d}})

    def test_resolve_creates_with_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "projects.json"
            cwd = os.getcwd()
            proj = resolve_project("demo", path=p)
            self.assertEqual(proj, {"name": "demo", "workdir": cwd})
            self.assertIn("demo", load_registry(p))

    def test_resolve_resumes_and_updates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "projects.json"
            w1 = str(Path(d) / "w1")
            self.assertEqual(resolve_project("demo", workdir=w1, path=p)
                             ["workdir"], os.path.abspath(w1))
            w2 = str(Path(d) / "w2")
            # explicit workdir updates the entry
            self.assertEqual(resolve_project("demo", workdir=w2, path=p)
                             ["workdir"], os.path.abspath(w2))
            # resume keeps the stored workdir
            self.assertEqual(resolve_project("demo", path=p)["workdir"],
                             os.path.abspath(w2))

    def test_session_marker(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(session_project(d))
            set_session_project(d, "demo")
            self.assertEqual(session_project(d), "demo")


class TestProjectNote(unittest.TestCase):
    def test_note_carries_project_line(self):
        d = tempfile.mkdtemp()
        s = Session(id="p", dir=Path(d), workdir=d)
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(100000, 80000), project="demo")
        loop.run_turn(s, "hi")
        sent = loop.provider.calls[0]["messages"]
        self.assertIn("[project: demo]", sent[-1]["content"])
        self.assertNotIn("[project:", s.context.load())


if __name__ == "__main__":
    unittest.main()
