import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness.__main__ import (_build_loop, _match_session,
                              parse_repl_command)
from harness.config import Config


def _args(**kw):
    base = dict(provider=None, model=None, request_timeout=None,
                budget_hard=None, budget_soft=None, exec_timeout=None,
                exec_timeout_max=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestJanitorKeyEnv(unittest.TestCase):
    """Issue #1: with no prune_* set, the janitor must inherit the main
    model's api_key_env (which may be custom), not the provider default."""

    def _cfg(self):
        return Config(provider="openai", model="gpt-5",
                      base_url="https://api.openai.com/v1",
                      api_key_env="MY_CUSTOM_KEY", api_key="sk-custom")

    def test_janitor_inherits_custom_main_key_env(self):
        cfg = self._cfg()
        with patch.dict(os.environ, {"MY_CUSTOM_KEY": "sk-custom"}):
            loop = _build_loop(cfg, _args())
        # Buggy code resolves OPENAI_API_KEY (unset) and sys.exits here;
        # fixed code picks up the custom key.
        self.assertEqual(loop.prune_provider.api_key, "sk-custom")

    def test_janitor_explicit_key_env_wins(self):
        cfg = self._cfg()
        cfg.prune_api_key_env = "JANITOR_KEY"
        with patch.dict(os.environ, {"MY_CUSTOM_KEY": "sk-custom",
                                     "JANITOR_KEY": "sk-janitor"}):
            loop = _build_loop(cfg, _args())
        self.assertEqual(loop.prune_provider.api_key, "sk-janitor")

    def test_janitor_other_provider_uses_its_default(self):
        cfg = self._cfg()
        cfg.prune_provider = "anthropic"
        with patch.dict(os.environ, {"MY_CUSTOM_KEY": "sk-custom",
                                     "ANTHROPIC_API_KEY": "sk-ant"}):
            loop = _build_loop(cfg, _args())
        self.assertEqual(loop.prune_provider.name, "anthropic")
        self.assertEqual(loop.prune_provider.api_key, "sk-ant")


class TestReplCommands(unittest.TestCase):
    def test_non_command(self):
        self.assertIsNone(parse_repl_command("hello world"))
        self.assertIsNone(parse_repl_command(""))

    def test_bare_commands(self):
        self.assertEqual(parse_repl_command("/list"), ("list", ""))
        self.assertEqual(parse_repl_command("/HELP"), ("help", ""))
        self.assertEqual(parse_repl_command("/"), None)

    def test_command_with_arg(self):
        self.assertEqual(parse_repl_command("/new do the thing"),
                         ("new", "do the thing"))
        self.assertEqual(parse_repl_command("/open 20261005"),
                         ("open", "20261005"))

    def test_match_session(self):
        with tempfile.TemporaryDirectory() as d:
            for sid in ("20261005-080000", "20261005-090000"):
                (Path(d) / sid).mkdir()
            cfg = SimpleNamespace(sessions_path=Path(d))
            self.assertEqual(
                _match_session(cfg, "20261005-080000"), "20261005-080000")
            self.assertEqual(
                _match_session(cfg, "20261005-09"), "20261005-090000")
            self.assertIsNone(_match_session(cfg, "20261005"))  # ambiguous
            self.assertIsNone(_match_session(cfg, "nope"))


if __name__ == "__main__":
    unittest.main()
