"""Red-first tests for T2: harness-owned stamps and turn counter.

Spec: docs/assembly-spec.md section 4; decisions 2026-10-09
(stamps stripped on the wire).
"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.context import (parse_transcript, render_history_assistant,
                             render_history_user, stamp)
from harness.session import (init_layout, load_state, next_section,
                             next_turn, save_state, turn_stamp)


class TestStamps(unittest.TestCase):
    def test_stamp_format(self):
        self.assertEqual(stamp(42), "t0042")
        self.assertEqual(stamp(0), "t0000")

    def test_next_turn_monotonic_and_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            init_layout(sdir, "P")
            self.assertEqual(next_turn(sdir), 1)
            self.assertEqual(next_turn(sdir), 2)
            # A fresh read sees the persisted counter (not model state).
            self.assertEqual(load_state(sdir)["turn"], 2)

    def test_next_section_independent_counter(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            init_layout(sdir, "P")
            self.assertEqual(next_section(sdir), 1)
            self.assertEqual(next_section(sdir), 2)
            self.assertEqual(load_state(sdir)["turn"], 0)

    def test_turn_stamp_reflects_counter(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            init_layout(sdir, "P")
            next_turn(sdir)
            next_turn(sdir)
            self.assertEqual(turn_stamp(sdir), "t0002")

    def test_load_state_corrupt_falls_back_to_zeros(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            init_layout(sdir, "P")
            (sdir / "state.json").write_text("not json", encoding="utf-8")
            self.assertEqual(load_state(sdir),
                             {"turn": 0, "section_seq": 0})

    def test_stamped_headers_parse_and_strip_on_wire(self):
        text = (render_history_user("do the thing", 7)
                + render_history_assistant("on it", 7))
        msgs = parse_transcript(text)
        self.assertEqual(len(msgs), 2)
        # Stamps never reach the wire (decision 4).
        self.assertEqual(msgs[0], {"role": "user", "content": "do the thing"})
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["content"], "on it")

    def test_stamped_headers_survive_section_split(self):
        from harness.context import _split_sections
        text = render_history_user("hi", 3)
        sections = _split_sections(text)
        self.assertEqual(sections[0][0], "user")
        self.assertIn("t0003", sections[0][2])  # header keeps the stamp

    def test_save_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            init_layout(sdir, "P")
            save_state(sdir, {"turn": 9, "section_seq": 4})
            self.assertEqual(load_state(sdir),
                             {"turn": 9, "section_seq": 4})


if __name__ == "__main__":
    unittest.main()
