import tempfile
import unittest
from pathlib import Path

from harness.context import Budget, parse_transcript
from harness.loop import Loop, Session
from harness.providers import MockProvider


def make_session(workdir=None):
    d = tempfile.mkdtemp()
    return Session(id="e", dir=Path(d), workdir=workdir or d)


class TestEchoSanitize(unittest.TestCase):
    def test_echoing_reply_stored_clean(self):
        s = make_session()
        echo = ("## user\nwell hello\n## assistant\n"
                "Well hello there! How can I help?")
        events = []
        loop = Loop(MockProvider([{"content": echo}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append((k, v)))
        out = loop.run_turn(s, "well hello")
        self.assertNotIn("## user", out)
        self.assertIn("Well hello", out)
        # The stored reply (archived at episode close) parses back
        # clean: the echoed ## headers were stripped before storing.
        archived = "".join(
            p.read_text(encoding="utf-8")
            for p in (s.dir / "archive").glob("*.md"))
        msgs = parse_transcript(archived)
        assistants = [m for m in msgs if m["role"] == "assistant"]
        self.assertTrue(assistants)
        self.assertNotIn("##", assistants[-1]["content"])

    def test_fenced_echo_stripped(self):
        s = make_session()
        echo = ("hi\n```tool-calls\n"
                "[{\"id\": \"x\", \"name\": \"exec\", "
                "\"arguments\": {}}]\n```")
        loop = Loop(MockProvider([{"content": echo}]),
                    Budget(hard=100000, soft=80000))
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "hi")


if __name__ == "__main__":
    unittest.main()
