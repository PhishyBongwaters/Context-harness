"""Red-first tests for detect_context_window.

Based on official llama.cpp docs:
- /props returns {"default_generation_settings": {"n_ctx": ...}, ...}
- /v1/models returns {"data": [{"id": ..., "meta": {"n_ctx": ...}}]}

Prior art: pi-llama-cpp, maverobot/qwen36-mtp opencode workaround.
"""
import json
import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO


class TestDetectContextWindow(unittest.TestCase):
    def _mock_urlopen(self, responses):
        """responses: dict mapping URL substring -> response dict."""
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            for key, resp_data in responses.items():
                if key in url:
                    body = json.dumps(resp_data).encode()
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = body
                    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                    mock_resp.__exit__ = MagicMock(return_value=False)
                    return mock_resp
            raise ValueError(f"No mock for {url}")
        return fake_urlopen

    def test_props_nested_n_ctx(self):
        """llama.cpp /props nests n_ctx under default_generation_settings."""
        from harness.config import detect_context_window
        props_resp = {
            "default_generation_settings": {
                "n_ctx": 135168,
                "params": {},
            },
            "total_slots": 1,
            "model_path": "/models/test.gguf",
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/props": props_resp,
                                       "/v1/models": {"data": []}})):
            result = detect_context_window("http://localhost:8080", "test")
            self.assertEqual(result, 135168)

    def test_v1_models_meta_n_ctx(self):
        """/v1/models meta.n_ctx takes priority."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [
                {"id": "test-model",
                 "meta": {"n_ctx": 32768}},
            ]
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/v1/models": models_resp})):
            result = detect_context_window("http://localhost:8080",
                                           "test-model")
            self.assertEqual(result, 32768)

    def test_v1_models_context_length_fallback(self):
        """LM Studio-style context_length field."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [
                {"id": "test-model",
                 "context_length": 262144},
            ]
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/v1/models": models_resp})):
            result = detect_context_window("http://localhost:8080",
                                           "test-model")
            self.assertEqual(result, 262144)

    def test_v1_with_trailing_slash(self):
        """base_url with /v1 suffix must not produce /v1/v1/models."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [{"id": "m", "meta": {"n_ctx": 65536}}]
        }
        seen_urls = []
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen_urls.append(url)
            body = json.dumps(models_resp).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = body
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        with patch("urllib.request.urlopen", fake_urlopen):
            result = detect_context_window("http://localhost:8080/v1", "m")
            self.assertEqual(result, 65536)
            # Verify no /v1/v1 in URL
            for u in seen_urls:
                self.assertNotIn("/v1/v1", u)


class TestAutosizeBudget(unittest.TestCase):
    """_autosize_budget applies live values only, honors pinned budgets."""

    def _cfg_loop(self, **kw):
        from types import SimpleNamespace
        from harness.config import Config
        from harness.context import Budget
        cfg = Config(provider=kw.get("provider", "openai"),
                     model=kw.get("model", "m"),
                     base_url=kw.get("base_url", "http://localhost:8080/v1"),
                     api_key_env=kw.get("api_key_env", "OPENAI_API_KEY"),
                     budget_hard=kw.get("budget_hard", 100_000),
                     budget_soft=kw.get("budget_soft", 80_000),
                     budget_hard_auto=kw.get("budget_hard_auto", True),
                     budget_soft_auto=kw.get("budget_soft_auto", True))
        loop = SimpleNamespace(budget=Budget(cfg.budget_hard,
                                             cfg.budget_soft))
        return cfg, loop

    def test_applies_live_window(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop()
        with patch.object(cli, "detect_context_window",
                          return_value=131072) as det:
            cli._autosize_budget(cfg, loop, "m")
        det.assert_called_once()
        self.assertEqual(cfg.budget_hard, 131072)
        self.assertEqual(cfg.budget_soft, int(131072 * 0.8))
        self.assertEqual(loop.budget.hard, 131072)
        self.assertEqual(loop.budget.soft, int(131072 * 0.8))

    def test_pinned_hard_keeps_value_soft_tracks_hard(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop(budget_hard=50_000,
                                   budget_hard_auto=False)
        with patch.object(cli, "detect_context_window",
                          return_value=131072):
            cli._autosize_budget(cfg, loop, "m")
        self.assertEqual(cfg.budget_hard, 50_000)
        self.assertEqual(cfg.budget_soft, int(50_000 * 0.8))
        self.assertEqual(loop.budget.hard, 50_000)  # untouched
        self.assertEqual(loop.budget.soft, int(50_000 * 0.8))

    def test_none_no_reset_by_default(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop(budget_hard=135168,
                                   budget_soft=int(135168 * 0.8))
        with patch.object(cli, "detect_context_window",
                          return_value=None):
            cli._autosize_budget(cfg, loop, "m")
        self.assertEqual((cfg.budget_hard, cfg.budget_soft),
                         (135168, int(135168 * 0.8)))

    def test_none_with_reset_falls_back_to_defaults(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop(budget_hard=135168,
                                   budget_soft=int(135168 * 0.8))
        with patch.object(cli, "detect_context_window",
                          return_value=None):
            cli._autosize_budget(cfg, loop, "cloud-model", reset=True)
        self.assertEqual((cfg.budget_hard, cfg.budget_soft),
                         (100_000, 80_000))
        self.assertEqual((loop.budget.hard, loop.budget.soft),
                         (100_000, 80_000))

    def test_reset_keeps_pinned_hard_soft_tracks_it(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop(budget_hard=50_000,
                                   budget_soft=int(135168 * 0.8),
                                   budget_hard_auto=False)
        with patch.object(cli, "detect_context_window",
                          return_value=None):
            cli._autosize_budget(cfg, loop, "cloud-model", reset=True)
        self.assertEqual(cfg.budget_hard, 50_000)
        self.assertEqual(cfg.budget_soft, int(50_000 * 0.8))

    def test_passes_kind_for_anthropic(self):
        from harness import __main__ as cli
        cfg, loop = self._cfg_loop(
            provider="anthropic", model="claude-x",
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY")
        with patch.object(cli, "detect_context_window",
                          return_value=200_000) as det:
            cli._autosize_budget(cfg, loop, "claude-x")
        _, kwargs = det.call_args
        self.assertEqual(kwargs.get("kind"), "anthropic")
        self.assertEqual(cfg.budget_hard, 200_000)


class TestBudgetChangeLines(unittest.TestCase):
    def _cfg(self, hard, soft, auto=True):
        from harness.config import Config
        return Config(provider="openai", model="m",
                      base_url="http://localhost:8080/v1",
                      api_key_env="OPENAI_API_KEY",
                      budget_hard=hard, budget_soft=soft,
                      budget_hard_auto=auto, budget_soft_auto=auto)

    def test_live_change(self):
        from harness import __main__ as cli
        cfg = self._cfg(135168, int(135168 * 0.8))
        lines = cli._budget_change_lines(cfg, (100_000, 80_000), "m")
        self.assertEqual(len(lines), 1)
        self.assertIn("135,168", lines[0])
        self.assertNotIn("no live window", lines[0])

    def test_reset_to_default_reports_undetected(self):
        from harness import __main__ as cli
        cfg = self._cfg(100_000, 80_000)
        lines = cli._budget_change_lines(
            cfg, (135168, int(135168 * 0.8)), "cloud-m")
        self.assertEqual(len(lines), 1)
        self.assertIn("no live window", lines[0])

    def test_unchanged_live_is_silent(self):
        from harness import __main__ as cli
        cfg = self._cfg(135168, int(135168 * 0.8))
        self.assertEqual(
            cli._budget_change_lines(
                cfg, (135168, int(135168 * 0.8)), "m"), [])

    def test_unchanged_defaults_reports_undetected(self):
        from harness import __main__ as cli
        cfg = self._cfg(100_000, 80_000)
        lines = cli._budget_change_lines(cfg, (100_000, 80_000), "m")
        self.assertEqual(len(lines), 1)
        self.assertIn("no live window", lines[0])

    def test_pinned_stays_silent(self):
        from harness import __main__ as cli
        cfg = self._cfg(50_000, 40_000, auto=False)
        self.assertEqual(
            cli._budget_change_lines(cfg, (50_000, 40_000), "m"), [])


class TestStartupProbe(unittest.TestCase):
    """_startup_probe: localhost-only, bounded, skips cloud/pinned."""

    def _cfg(self, **kw):
        from harness.config import Config
        return Config(
            provider=kw.get("provider", "openai"),
            model=kw.get("model", "m"),
            base_url=kw.get("base_url", "http://127.0.0.1:8080/v1"),
            api_key_env=kw.get("api_key_env", "OPENAI_API_KEY"),
            budget_hard=kw.get("budget_hard", 100_000),
            budget_soft=kw.get("budget_soft", 80_000),
            budget_hard_auto=kw.get("budget_hard_auto", True),
            budget_soft_auto=kw.get("budget_soft_auto", True))

    def test_local_applies_live_window(self):
        from harness import __main__ as cli
        from harness.context import Budget
        cfg = self._cfg()
        loop = {"budget": Budget(cfg.budget_hard, cfg.budget_soft)}
        loop = type("L", (), loop)()
        with patch.object(cli, "detect_context_window",
                          return_value=135168) as det:
            changed = cli._startup_probe(cfg, loop)
        self.assertTrue(changed)
        self.assertEqual((cfg.budget_hard, cfg.budget_soft),
                         (135168, int(135168 * 0.8)))
        _, kwargs = det.call_args
        self.assertEqual(kwargs.get("timeout"), 2)

    def test_cloud_skipped_without_network(self):
        from harness import __main__ as cli
        cfg = self._cfg(base_url="https://api.openai.com/v1")
        with patch.object(cli, "detect_context_window") as det:
            changed = cli._startup_probe(cfg, None)
        det.assert_not_called()
        self.assertFalse(changed)
        self.assertEqual((cfg.budget_hard, cfg.budget_soft),
                         (100_000, 80_000))

    def test_pinned_skipped_without_network(self):
        from harness import __main__ as cli
        cfg = self._cfg(budget_hard=50_000, budget_hard_auto=False,
                        budget_soft_auto=False)
        with patch.object(cli, "detect_context_window") as det:
            changed = cli._startup_probe(cfg, None)
        det.assert_not_called()
        self.assertFalse(changed)

    def test_never_raises(self):
        from harness import __main__ as cli
        cfg = self._cfg()
        with patch.object(cli, "detect_context_window",
                          side_effect=RuntimeError("boom")):
            self.assertFalse(cli._startup_probe(cfg, None))


if __name__ == "__main__":
    unittest.main()
