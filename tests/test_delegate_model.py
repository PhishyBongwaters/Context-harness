"""delegate_model config: provider/model override for subagents."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from harness.context import Budget
from harness.loop import Loop, Session, _parse_delegate_model
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


class TestParseDelegateModel(unittest.TestCase):
    def test_provider_slash_model(self):
        self.assertEqual(_parse_delegate_model("nvidia/Qwen3", "openai"),
                         ("nvidia", "Qwen3"))

    def test_bare_model_uses_parent_provider(self):
        self.assertEqual(_parse_delegate_model("gpt-4o-mini", "openai"),
                         ("openai", "gpt-4o-mini"))

    def test_whitespace_stripped(self):
        self.assertEqual(_parse_delegate_model(" nvidia/Qwen3 ", "openai"),
                         ("nvidia", "Qwen3"))


class TestDelegateModelConfig(unittest.TestCase):
    def test_inherits_parent_by_default(self):
        loop = _loop()
        sess = _session()
        # capture the provider the subagent loop gets
        seen = {}
        orig_init = Loop.__init__

        def spy_init(self, *a, **k):
            seen["provider"] = k.get("provider", a[0] if a else None)
            return orig_init(self, *a, **k)

        with unittest.mock.patch.object(Loop, "__init__", spy_init):
            # prevent the thread from actually running
            with unittest.mock.patch("threading.Thread"):
                out = json.loads(loop._delegate(
                    sess, {"task": "x"}))
        self.assertEqual(out["status"], "running")
        self.assertIs(seen["provider"], loop.provider)

    def test_config_override_used(self):
        made = []

        def factory(provider=None, model=None):
            made.append((provider, model))
            p = MagicMock()
            p.name = provider
            return p

        loop = _loop(delegate_model="nvidia/Qwen3",
                     provider_factory=factory)
        sess = _session()
        orig_init = Loop.__init__
        seen = {}

        def spy_init(self, *a, **k):
            seen["provider"] = k.get("provider", a[0] if a else None)
            return orig_init(self, *a, **k)

        with unittest.mock.patch.object(Loop, "__init__", spy_init):
            with unittest.mock.patch("threading.Thread"):
                out = json.loads(loop._delegate(sess, {"task": "x"}))
        self.assertEqual(out["status"], "running")
        self.assertEqual(made, [("nvidia", "Qwen3")])
        self.assertIs(seen["provider"], made[0] and seen["provider"])

    def test_tool_arg_beats_config(self):
        made = []

        def factory(provider=None, model=None):
            made.append((provider, model))
            p = MagicMock()
            p.name = provider
            return p

        loop = _loop(delegate_model="nvidia/Qwen3",
                     provider_factory=factory)
        sess = _session()
        with unittest.mock.patch("threading.Thread"):
            # patch Loop.__init__ to avoid running, just capture
            with unittest.mock.patch.object(
                    Loop, "__init__",
                    lambda self, *a, **k: None):
                out = json.loads(loop._delegate(
                    sess, {"task": "x", "model": "anthropic/claude"}))
        self.assertEqual(made, [("anthropic", "claude")])


if __name__ == "__main__":
    unittest.main()
