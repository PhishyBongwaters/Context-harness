"""Circuit breaker: 3 denials of the same call -> hard stop."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from harness.context import Budget
from harness.loop import Loop, Session
from harness.session import init_layout


def _session():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return Session(id="t", dir=d, workdir=str(d))


def _loop(**kw):
    provider = MagicMock()
    provider.name = "openai"
    return Loop(provider=provider,
                budget=Budget(hard=100_000, soft=80_000),
                approver=None, usage_tracker=None, **kw)


class TestCircuitBreaker(unittest.TestCase):
    def test_three_denials_trips_breaker(self):
        sess = _session()
        # subagent trying delegate -> "status": "error", 3 times
        tc = {"name": "delegate", "arguments": {"task": "x"}}
        sub = _loop(is_subagent=True)
        for i in range(3):
            r = sub._execute_tool(sess, tc)
            self.assertIn('"status": "error"', r)
            self.assertNotIn("CIRCUIT BREAKER", r)
        # 4th identical call: circuit breaker, not executed
        r = sub._execute_tool(sess, tc)
        self.assertIn("CIRCUIT BREAKER", r)

    def test_success_resets_count(self):
        loop = _loop()
        sess = _session()
        tc = {"name": "bogus_tool", "arguments": {}}
        for _ in range(2):
            r = loop._execute_tool(sess, tc)
            self.assertIn("ERROR", r)
        # a successful call with different args resets that key only;
        # same key still at 2
        ok_tc = {"name": "exec", "arguments": {"command": "echo hi"}}
        r = loop._execute_tool(sess, ok_tc)
        self.assertNotIn("ERROR", r)
        # third denial of the original -> still counts (was 2)
        r = loop._execute_tool(sess, tc)
        self.assertIn("ERROR", r)
        self.assertNotIn("CIRCUIT BREAKER", r)
        # fourth -> breaker
        r = loop._execute_tool(sess, tc)
        self.assertIn("CIRCUIT BREAKER", r)

    def test_different_args_still_grouped(self):
        # Varying the arguments after a denial is still spamming: the
        # breaker keys on tool name, not args.
        loop = _loop()
        sess = _session()
        for i in range(3):
            tc = {"name": "bogus_tool", "arguments": {"n": i}}
            r = loop._execute_tool(sess, tc)
            self.assertNotIn("CIRCUIT BREAKER", r)
        r = loop._execute_tool(sess, {"name": "bogus_tool",
                                      "arguments": {"n": 99}})
        self.assertIn("CIRCUIT BREAKER", r)

    def test_different_tool_not_blocked(self):
        loop = _loop()
        sess = _session()
        for _ in range(3):
            loop._execute_tool(sess, {"name": "bogus_a",
                                      "arguments": {}})
        # bogus_b is unaffected
        r = loop._execute_tool(sess, {"name": "bogus_b",
                                      "arguments": {}})
        self.assertNotIn("CIRCUIT BREAKER", r)
        self.assertIn("ERROR", r)


if __name__ == "__main__":
    unittest.main()
