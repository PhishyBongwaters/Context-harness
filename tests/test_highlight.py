"""Fenced code-block syntax highlighting for the transcript.

highlight_fenced_code() renders ```lang blocks through Rich Syntax to
ANSI (like a code editor), falling back to the raw text whenever Rich
is missing or a lexer blows up. strip_ansi() keeps exported transcripts
readable in plain editors.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import harness.tui.widgets as w


class TestStripAnsi(unittest.TestCase):
    def test_strips_sgr(self):
        self.assertEqual(w.strip_ansi("\x1b[1;32mhi\x1b[0m"), "hi")

    def test_plain_untouched(self):
        self.assertEqual(w.strip_ansi("plain text"), "plain text")

    def test_empty_safe(self):
        self.assertEqual(w.strip_ansi(""), "")
        self.assertEqual(w.strip_ansi(None), "")


class TestHighlightFencedCode(unittest.TestCase):
    def test_no_fence_unchanged(self):
        text = "just some chat text\nno code here"
        self.assertEqual(w.highlight_fenced_code(text), text)

    def test_unclosed_fence_unchanged(self):
        text = "```python\nprint('hi')\nno closing fence"
        self.assertEqual(w.highlight_fenced_code(text), text)

    def test_rich_missing_returns_text(self):
        text = "```python\nprint('hi')\n```"
        with patch.object(w, "_RICH_AVAILABLE", False):
            self.assertEqual(w.highlight_fenced_code(text), text)

    def test_python_fence_highlighted(self):
        calls = {}

        class FakeConsole:
            def __init__(self, **kw):
                pass

            def print(self, syn):
                calls["syntax"] = syn

            def export_text(self, styles=True):
                return "\x1b[32mprint('hi')\x1b[0m\n"

        def fake_syntax(code, lexer, **kw):
            calls["lexer"] = lexer
            return ("syntax", code)

        text = "before\n```python\nprint('hi')\n```\nafter"
        with patch.object(w, "_RICH_AVAILABLE", True), \
             patch.object(w, "Console", FakeConsole), \
             patch.object(w, "Syntax", fake_syntax):
            out = w.highlight_fenced_code(text)
        self.assertNotIn("```", out)
        self.assertIn("before", out)
        self.assertIn("after", out)
        self.assertIn("\x1b[32m", out)
        self.assertEqual(calls["lexer"], "python")

    def test_unknown_lexer_falls_back(self):
        def boom(code, lexer, **kw):
            raise ValueError("no such lexer")

        text = "```blargh\nsome text\n```"
        with patch.object(w, "_RICH_AVAILABLE", True), \
             patch.object(w, "Syntax", boom):
            out = w.highlight_fenced_code(text)
        # original fence survives instead of crashing
        self.assertIn("```blargh", out)


if __name__ == "__main__":
    unittest.main()
