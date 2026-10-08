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


class TestBuildLoopWindow(unittest.TestCase):
    def test_loop_budget_carries_window(self):
        cfg = Config(provider="openai", model="m",
                     base_url="http://127.0.0.1:8080/v1",
                     api_key_env=None, api_key=None,
                     budget_hard=67584, budget_soft=54067,
                     context_window=135168)
        loop = _build_loop(cfg, _args())
        self.assertEqual(loop.budget.window, 135168)
        self.assertEqual(loop.budget.denom(), 135168)

    def test_loop_budget_window_defaults_none(self):
        cfg = Config(provider="openai", model="m",
                     base_url="http://127.0.0.1:8080/v1",
                     api_key_env=None, api_key=None,
                     budget_hard=50000, budget_soft=40000)
        loop = _build_loop(cfg, _args())
        self.assertIsNone(loop.budget.window)
        self.assertEqual(loop.budget.denom(), 50000)


class TestPruneTimeout(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(provider="openai", model="m",
                    base_url="http://127.0.0.1:8080/v1",
                    api_key_env=None, api_key=None,
                    budget_hard=50000, budget_soft=40000,
                    request_timeout=120, prune_request_timeout=None)
        base.update(kw)
        return Config(**base)

    def test_default_is_3x_request_timeout(self):
        from harness import __main__ as cli
        self.assertEqual(cli._prune_timeout(self._cfg()), 360)

    def test_explicit_wins(self):
        from harness import __main__ as cli
        self.assertEqual(
            cli._prune_timeout(self._cfg(prune_request_timeout=600)),
            600)

    def test_build_loop_prune_provider_uses_prune_timeout(self):
        loop = _build_loop(self._cfg(), _args())
        self.assertEqual(loop.provider.timeout, 120)
        self.assertEqual(loop.prune_provider.timeout, 360)

    def test_make_provider_timeout_override(self):
        from harness.providers import make_provider
        cfg = self._cfg()
        self.assertEqual(make_provider(cfg).timeout, 120)
        self.assertEqual(make_provider(cfg, timeout=45).timeout, 45)


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
