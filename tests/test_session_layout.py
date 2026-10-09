"""Red-first tests for T1: blank-slate session on-disk layout.

Spec: docs/assembly-spec.md sections 2 and 5 (source table).
"""
import json
import unittest

from harness.session import init_layout


def fresh_layout(tmp_path, **kw):
    return init_layout(tmp_path, **kw)


class TestSessionLayout(unittest.TestCase):
    def test_creates_all_files_and_dirs(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="PROMPT")
            for key in ("prompt", "state", "index", "history",
                        "scratch"):
                self.assertTrue(paths[key].is_file(), key)
            for key in ("facts", "decisions", "tasks"):
                self.assertTrue(paths[key].is_file(), key)
            self.assertTrue(paths["archive"].is_dir())

    def test_prompt_text_stored_verbatim(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="hello {ctx_path}")
            self.assertEqual(paths["prompt"].read_text(encoding="utf-8"),
                             "hello {ctx_path}")

    def test_state_json_initial_counters(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="P")
            state = json.loads(paths["state"].read_text(encoding="utf-8"))
            self.assertEqual(state["turn"], 0)
            self.assertEqual(state["section_seq"], 0)

    def test_index_has_holy_sections(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="P")
            text = paths["index"].read_text(encoding="utf-8")
            self.assertIn("satellites", text)
            self.assertIn("episodes", text)

    def test_scratch_starts_empty(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="P")
            self.assertEqual(paths["scratch"].read_text(encoding="utf-8"),
                             "")

    def test_idempotent_does_not_clobber(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "sess"
            paths = fresh_layout(sdir, prompt_text="P")
            # Simulate real use: history has content, turn counter moved.
            paths["history"].write_text("## user t0001\nhi\n",
                                        encoding="utf-8")
            paths["state"].write_text(json.dumps({"turn": 7,
                                                  "section_seq": 3}),
                                       encoding="utf-8")
            paths2 = fresh_layout(sdir, prompt_text="P")
            self.assertEqual(paths2["history"].read_text(encoding="utf-8"),
                             "## user t0001\nhi\n")
            state = json.loads(paths2["state"].read_text(encoding="utf-8"))
            self.assertEqual(state["turn"], 7)


if __name__ == "__main__":
    unittest.main()
