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
        # Stored transcript parses back to exactly user + assistant.
        msgs = parse_transcript(s.context.load())
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertNotIn("##", msgs[1]["content"])

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
