import unittest

from harness.context import (parse_transcript, render_assistant, render_tool,
                             render_user)


def big_transcript(n=100):
    return "\n".join(f"## user\n{'x' * 200}" for _ in range(n)) + "\n"


class TestTranscriptFormat(unittest.TestCase):
    def test_roundtrip(self):
        text = (
            render_user("do the thing")
            + render_assistant("on it", [{"id": "c1", "name": "exec",
                                          "arguments": {"command": "ls"}}])
            + render_tool("c1", "exit=0\nfile.txt\n")
            + render_assistant("done", None)
        )
        msgs = parse_transcript(text)
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[0], {"role": "user", "content": "do the thing"})
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertEqual(msgs[1]["content"], "on it")
        self.assertEqual(msgs[1]["tool_calls"][0]["name"], "exec")
        self.assertEqual(msgs[1]["tool_calls"][0]["arguments"],
                         {"command": "ls"})
        self.assertEqual(msgs[2], {"role": "tool", "tool_call_id": "c1",
                                   "content": "exit=0\nfile.txt"})
        self.assertEqual(msgs[3]["content"], "done")
        self.assertIsNone(msgs[3]["tool_calls"])

    def test_preamble_ignored(self):
        msgs = parse_transcript("# notes\nkeep this\n" + render_user("hi"))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "hi")

    def test_orphan_tool_result_dropped(self):
        text = render_user("hi") + "## tool zz\nstale result\n"
        msgs = parse_transcript(text)
        self.assertEqual(len(msgs), 1)  # only the user message survives

    def test_broken_fence_is_plain_text(self):
        text = ("## assistant\nthinking\n```tool-calls\n{not json\n```\n"
                + render_user("hi"))
        msgs = parse_transcript(text)
        self.assertEqual(len(msgs), 2)
        self.assertIsNone(msgs[0]["tool_calls"])
        self.assertIn("thinking", msgs[0]["content"])

    def test_model_edit_simulation(self):
        # model deletes a stale section via edit; re-parse sees the change
        text = (render_user("old question")
                + render_assistant("old answer", None)
                + render_user("new question"))
        pruned = text.replace(render_user("old question")
                              + render_assistant("old answer", None), "")
        msgs = parse_transcript(pruned)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "new question")

    def test_tool_label_with_annotation(self):
        text = (render_user("x")
                + render_assistant(None, [{"id": "c7", "name": "read",
                                           "arguments": {}}])
                + "## tool c7 (read output)\ncontent here\n")
        msgs = parse_transcript(text)
        tool = [m for m in msgs if m["role"] == "tool"][0]
        self.assertEqual(tool["tool_call_id"], "c7")


if __name__ == "__main__":
    unittest.main()
