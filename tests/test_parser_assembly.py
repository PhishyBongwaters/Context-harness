"""T9: parser verification on assembled output.

parse_transcript against realistic multi-source assemblies: sats,
stamped history, episode pointers, scratch tool fences. Verifies role
order, tool-call linkage, orphan dropping, stamp stripping, and that
the loop's _transcript_messages agrees with a fresh parse.
"""
import sys
import unittest

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.assembly import assemble
from harness.context import parse_transcript
from harness.loop import Budget, Loop
from harness.providers import MockProvider


FULL_ASSEMBLY = """## sat facts
user likes dark mode

## sat decisions
chose sqlite for local state

## sat tasks
ship T9

## user t0001
what is the answer?

## episode t0001
archive: archive/20261009-t0001.md
turns: t0001-t0001
tool_calls: 1

## assistant
let me check
```tool-calls
[{"id": "e1", "name": "exec", "arguments": {"command": "echo 42"}}]
```
## tool e1
exit=0
42

## tool orphan1
this has no matching fence

## assistant
the answer is 42
"""


class TestParserOnAssembly(unittest.TestCase):
    def test_full_assembly_role_order(self):
        msgs = parse_transcript(FULL_ASSEMBLY)
        self.assertEqual(
            [m["role"] for m in msgs],
            ["user", "user", "user",      # sats
             "user",                       # history user
             "user",                       # episode pointer
             "assistant", "tool",          # scratch episode
             "assistant"])

    def test_tool_call_linkage(self):
        msgs = parse_transcript(FULL_ASSEMBLY)
        asst = msgs[5]
        self.assertEqual([t["id"] for t in asst["tool_calls"]], ["e1"])
        self.assertEqual(asst["content"], "let me check")
        tool = msgs[6]
        self.assertEqual(tool["tool_call_id"], "e1")
        self.assertIn("42", tool["content"])

    def test_orphan_tool_result_dropped(self):
        msgs = parse_transcript(FULL_ASSEMBLY)
        bodies = [m.get("content") or "" for m in msgs]
        self.assertFalse(any("no matching fence" in b for b in bodies))

    def test_stamps_stripped_from_wire(self):
        msgs = parse_transcript(FULL_ASSEMBLY)
        user = msgs[3]
        self.assertEqual(user["content"], "what is the answer?")
        self.assertNotIn("t0001", user["content"])

    def test_episode_pointer_is_user_message(self):
        msgs = parse_transcript(FULL_ASSEMBLY)
        ptr = msgs[4]
        self.assertEqual(ptr["role"], "user")
        self.assertIn("archive/20261009-t0001.md", ptr["content"])
        self.assertIn("tool_calls: 1", ptr["content"])

    def test_malformed_fence_is_safe(self):
        text = ("## assistant\nbroken\n```tool-calls\nnot json\n```\n"
                "## user t0002\nhi\n")
        msgs = parse_transcript(text)
        self.assertIsNone(msgs[0].get("tool_calls"))
        self.assertIn("broken", msgs[0]["content"])
        self.assertEqual(msgs[1], {"role": "user", "content": "hi"})

    def test_loop_transcript_matches_fresh_parse(self):
        # What the loop sends == parse of the artifact it wrote.
        s = make_session()
        loop = Loop(MockProvider([
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "exec",
                 "arguments": {"command": "echo 42"}}]},
            {"content": "done"},
        ]), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        raw, messages = loop._transcript_messages(s)
        self.assertEqual(
            raw, (s.dir / "context.md").read_text(encoding="utf-8"))
        self.assertEqual(messages, parse_transcript(raw))
        # Post-close: sats + history users + episode pointer, no tools.
        roles = [m["role"] for m in messages]
        self.assertNotIn("tool", roles)
        self.assertTrue(any("archive/" in (m.get("content") or "")
                            for m in messages if m["role"] == "user"))

    def test_two_episode_pointers_in_order(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "a"}, {"content": "b"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "one")
        loop.run_turn(s, "two")
        msgs = parse_transcript(assemble(s.dir))
        ptrs = [m["content"] for m in msgs
                if m["role"] == "user" and "archive/" in (m["content"] or "")]
        self.assertEqual(len(ptrs), 2)
        self.assertIn("t0001", ptrs[0])
        self.assertIn("t0002", ptrs[1])


if __name__ == "__main__":
    unittest.main()
