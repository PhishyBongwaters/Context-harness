import json
import unittest

from harness.providers import (AnthropicProvider, MockProvider,
                               OpenAIProvider, ProviderError, make_provider)


class TestOpenAI(unittest.TestCase):
    def setUp(self):
        self.p = OpenAIProvider(api_key="k", model="m",
                                base_url="https://x.test/v1")

    def test_build_payload_shape(self):
        tools = [{"name": "exec", "description": "d",
                  "parameters": {"type": "object"}}]
        payload = self.p.build_payload(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=tools)
        self.assertEqual(payload["model"], "m")
        self.assertEqual(payload["messages"][0],
                         {"role": "system", "content": "sys"})
        self.assertEqual(payload["messages"][1]["role"], "user")
        fn = payload["tools"][0]
        self.assertEqual(fn["type"], "function")
        self.assertEqual(fn["function"]["name"], "exec")

    def test_parse_response(self):
        data = {"choices": [{"message": {
            "content": "hello",
            "tool_calls": [{"id": "c1", "function": {
                "name": "exec",
                "arguments": json.dumps({"command": "echo hi"})}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        out = self.p.parse_response(data)
        self.assertEqual(out["content"], "hello")
        self.assertEqual(out["tool_calls"][0]["name"], "exec")
        self.assertEqual(out["tool_calls"][0]["arguments"]["command"],
                         "echo hi")
        self.assertEqual(out["usage"], {"input": 10, "output": 5})

    def test_parse_response_no_tools(self):
        data = {"choices": [{"message": {"content": "done"}}], "usage": {}}
        out = self.p.parse_response(data)
        self.assertEqual(out["content"], "done")
        self.assertEqual(out["tool_calls"], [])


class TestAnthropic(unittest.TestCase):
    def setUp(self):
        self.p = AnthropicProvider(api_key="k", model="m")

    def test_translate_tool_result(self):
        msgs = self.p.translate_messages(
            [{"role": "tool", "tool_call_id": "t1", "content": "out"}])
        self.assertEqual(msgs[0]["role"], "user")
        block = msgs[0]["content"][0]
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "t1")

    def test_translate_assistant_tool_calls(self):
        msgs = self.p.translate_messages([{
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t1", "name": "read",
                            "arguments": {"path": "f"}}]}])
        blocks = msgs[0]["content"]
        self.assertEqual(blocks[0]["type"], "tool_use")
        self.assertEqual(blocks[0]["input"], {"path": "f"})

    def test_build_payload_shape(self):
        payload = self.p.build_payload(
            system="sys", messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "read", "description": "d",
                    "parameters": {"type": "object"}}])
        self.assertEqual(payload["system"], "sys")
        self.assertIn("max_tokens", payload)
        self.assertEqual(payload["tools"][0]["input_schema"],
                         {"type": "object"})

    def test_parse_response(self):
        data = {"content": [
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "t9", "name": "exec",
             "input": {"command": "ls"}}],
            "usage": {"input_tokens": 3, "output_tokens": 7}}
        out = self.p.parse_response(data)
        self.assertEqual(out["content"], "hi")
        self.assertEqual(out["tool_calls"][0]["id"], "t9")
        self.assertEqual(out["usage"], {"input": 3, "output": 7})


class TestMock(unittest.TestCase):
    def test_script_and_recording(self):
        m = MockProvider([{"content": "a"}, {"content": "b"}])
        r1 = m.chat(system="s", messages=[], tools=[])
        r2 = m.chat(system="s", messages=[], tools=[])
        self.assertEqual((r1["content"], r2["content"]), ("a", "b"))
        self.assertEqual(len(m.calls), 2)
        with self.assertRaises(ProviderError):
            m.chat(system="s", messages=[], tools=[])


class TestMakeProvider(unittest.TestCase):
    def test_unknown(self):
        class C:
            provider = "nope"
        with self.assertRaises(ProviderError):
            make_provider(C())


if __name__ == "__main__":
    unittest.main()
