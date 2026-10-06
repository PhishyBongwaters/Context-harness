"""Provider registry: resolution, templates, probe, persistence, CLI/TUI.

Stdlib-only except the pilot, which skips cleanly without the extra.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from harness import __main__ as cli
from harness.config import (PROVIDER_DEFAULTS, PROVIDER_TEMPLATES,
                            load_config, probe_provider, resolve_provider,
                            save_current_provider, save_provider_entry,
                            validate_provider_entry)
from harness.tui import has_tui


def make_cfg(**kw):
    base = {"provider": "openai", "model": "gpt-5",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY", "api_key": "k-openai",
            "prune_provider": None, "prune_model": None,
            "prune_base_url": None, "prune_api_key_env": None,
            "request_timeout": 120, "providers": {}}
    base.update(kw)
    return SimpleNamespace(**base)


def make_args(**kw):
    base = {"provider": None, "model": None, "base_url": None,
            "request_timeout": None, "budget_hard": None,
            "budget_soft": None}
    base.update(kw)
    return SimpleNamespace(**base)


def make_box(cfg):
    from harness.providers import make_provider
    loop = SimpleNamespace(provider=make_provider(cfg),
                           prune_provider=make_provider(cfg))
    return {"loop": loop}


LOCAL = {"kind": "openai", "base_url": "http://127.0.0.1:8080/v1",
         "model": "Qwen", "api_key_env": None}


class TestResolveProvider(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "k-openai"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_registry_hit(self):
        cfg = make_cfg(provider="mylocal",
                       providers={"mylocal": dict(LOCAL)})
        res = resolve_provider(cfg)
        self.assertTrue(res.registry_hit)
        self.assertEqual(res.kind, "openai")
        self.assertEqual(res.model, "Qwen")
        self.assertEqual(res.base_url, "http://127.0.0.1:8080/v1")
        self.assertIsNone(res.api_key_env)
        self.assertIsNone(res.api_key)

    def test_legacy_fallback_no_registry(self):
        cfg = make_cfg()
        res = resolve_provider(cfg)
        self.assertFalse(res.registry_hit)
        self.assertEqual(res.kind, "openai")
        self.assertEqual(res.base_url,
                         PROVIDER_DEFAULTS["openai"]["base_url"])
        self.assertEqual(res.api_key, "k-openai")

    def test_legacy_fallback_unknown_name(self):
        cfg = make_cfg(provider="nvidia")
        res = resolve_provider(cfg)
        self.assertFalse(res.registry_hit)
        self.assertEqual(res.kind, "nvidia")

    def test_per_field_fallback_to_kind_defaults(self):
        cfg = make_cfg(provider="e", model="top-model", providers={
            "e": {"kind": "openai", "model": "entry-model"}})
        res = resolve_provider(cfg)
        self.assertEqual(res.base_url,
                         PROVIDER_DEFAULTS["openai"]["base_url"])
        self.assertEqual(res.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(res.model, "entry-model")
        # Empty entry model falls back to the top-level model.
        cfg2 = make_cfg(provider="e", model="top-model", providers={
            "e": {"kind": "openai"}})
        self.assertEqual(resolve_provider(cfg2).model, "top-model")
        # Missing api_key_env key falls back; explicit null stays local.
        cfg3 = make_cfg(provider="e", providers={
            "e": {"kind": "openai", "model": "m",
                  "base_url": "http://x/v1"}})
        self.assertEqual(resolve_provider(cfg3).api_key_env,
                         "OPENAI_API_KEY")
        cfg4 = make_cfg(provider="e", providers={
            "e": {"kind": "openai", "model": "m",
                  "base_url": "http://x/v1", "api_key_env": None}})
        self.assertIsNone(resolve_provider(cfg4).api_key_env)


class TestTemplates(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(set(PROVIDER_TEMPLATES),
                         {"openai-cloud", "anthropic-cloud", "nvidia-cloud",
                          "llama.cpp", "lmstudio", "ollama"})
        for tid, tpl in PROVIDER_TEMPLATES.items():
            self.assertIn(tpl["kind"], PROVIDER_DEFAULTS, tid)
            self.assertTrue(tpl["base_url"], tid)
            self.assertIn("model", tpl, tid)
            self.assertIn("api_key_env", tpl, tid)
        clouds = ("openai-cloud", "anthropic-cloud", "nvidia-cloud")
        for tid in clouds:
            self.assertTrue(PROVIDER_TEMPLATES[tid]["api_key_env"], tid)
        for tid in ("llama.cpp", "lmstudio", "ollama"):
            self.assertEqual(PROVIDER_TEMPLATES[tid]["kind"], "openai")
            self.assertIsNone(PROVIDER_TEMPLATES[tid]["api_key_env"], tid)


class TestProbe(unittest.TestCase):
    def test_none_cases(self):
        self.assertIsNone(probe_provider(None, "openai"))
        self.assertIsNone(probe_provider("", "openai"))
        self.assertIsNone(probe_provider("https://x/v1", "anthropic"))

    def test_true_on_success(self):
        fake_resp = mock.MagicMock()
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.read.return_value = b"{}"
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_resp) as uo:
            self.assertTrue(probe_provider("http://x/v1", "openai"))
            called_url = uo.call_args[0][0]
            self.assertEqual(called_url.full_url, "http://x/v1/models")

    def test_false_on_failure(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("refused")):
            self.assertFalse(probe_provider("http://x/v1", "openai"))
        import io
        err = urllib.error.HTTPError(
            "http://x/v1/models", 401, "deny", {}, io.BytesIO(b"denied"))
        try:
            with mock.patch("urllib.request.urlopen", side_effect=err):
                self.assertFalse(probe_provider("http://x/v1", "openai"))
        finally:
            err.close()


class TestSaveProviderEntry(unittest.TestCase):
    def test_round_trip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"provider": "openai",
                                     "weird": {"a": 1},
                                     "providers": {}}))
            save_provider_entry(p, "mylocal", dict(LOCAL))
            raw = json.loads(p.read_text())
            self.assertEqual(raw["provider"], "mylocal")
            self.assertEqual(raw["weird"], {"a": 1})
            self.assertEqual(raw["providers"]["mylocal"]["model"], "Qwen")
            # No activate: provider untouched.
            save_provider_entry(p, "second",
                                {**LOCAL, "model": "M2"}, activate=False)
            raw = json.loads(p.read_text())
            self.assertEqual(raw["provider"], "mylocal")
            self.assertIn("second", raw["providers"])

    def test_load_config_reads_registry(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps(
                {"provider": "mylocal",
                 "providers": {"mylocal": dict(LOCAL)}}))
            cfg = load_config(path=p)
            self.assertEqual(cfg.provider, "mylocal")
            self.assertEqual(cfg.model, "Qwen")
            self.assertEqual(cfg.base_url, "http://127.0.0.1:8080/v1")
            self.assertIsNone(cfg.api_key_env)
            self.assertEqual(cfg.providers["mylocal"]["kind"], "openai")

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            validate_provider_entry("  ", dict(LOCAL), {})
        with self.assertRaises(ValueError):
            validate_provider_entry("x", {**LOCAL, "kind": "bogus"}, {})
        with self.assertRaises(ValueError):
            validate_provider_entry("x", {**LOCAL, "model": "  "}, {})
        with self.assertRaises(ValueError):
            validate_provider_entry("x", dict(LOCAL), {"x": {} },
                                    require_unique=True)
        # Cleaning: whitespace stripped, blank url/key normalized.
        clean = validate_provider_entry(
            " n ", {"kind": "openai", "base_url": "",
                    "model": " m ", "api_key_env": ""})
        self.assertEqual(clean, {"kind": "openai", "base_url": None,
                                 "model": "m", "api_key_env": None})


class TestProviderOverride(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "k-openai"
        os.environ["NVIDIA_API_KEY"] = "k-nvidia"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_registry_name_resolution_and_base_url(self):
        cfg = make_cfg(providers={"fast": {
            "kind": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "nemotron", "api_key_env": "NVIDIA_API_KEY"}})
        box = make_box(cfg)
        with mock.patch.object(cli, "config_path",
                               return_value=Path("/nonexistent/c.json")):
            # Persist fails safely (no dir) but in-memory retarget works.
            cli.retarget_loop(box, cfg, make_args(), "fast", "nemotron-2")
        self.assertEqual(cfg.provider, "fast")
        self.assertEqual(cfg.model, "nemotron-2")
        self.assertEqual(cfg.base_url,
                         "https://integrate.api.nvidia.com/v1")
        self.assertEqual(cfg.api_key, "k-nvidia")
        self.assertEqual(cfg.providers["fast"]["model"], "nemotron-2")
        self.assertEqual(box["loop"].provider.model, "nemotron-2")

    def test_registry_retarget_persists_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.json"
            target.write_text(json.dumps(
                {"provider": "fast",
                 "providers": {"fast": {
                     "kind": "openai", "base_url": "http://x/v1",
                     "model": "m1", "api_key_env": None}}}))
            cfg = load_config(path=target)
            from harness.providers import OpenAIProvider
            box = {"loop": SimpleNamespace(
                provider=OpenAIProvider(api_key=None, model="m1",
                                        base_url="http://x/v1"),
                prune_provider=OpenAIProvider(api_key=None, model="m1",
                                              base_url="http://x/v1"))}
            with mock.patch.object(cli, "config_path",
                                   return_value=target):
                cli.retarget_loop(box, cfg, make_args(), "fast", "m2")
            raw = json.loads(target.read_text())
            self.assertEqual(raw["providers"]["fast"]["model"], "m2")
            self.assertEqual(raw["provider"], "fast")

    def test_legacy_base_url_reset_unchanged(self):
        cfg = make_cfg(base_url="http://localhost:1234/v1")
        box = make_box(cfg)
        cli.retarget_loop(box, cfg, make_args(), "nvidia", "nv-model")
        self.assertEqual(cfg.base_url,
                         PROVIDER_DEFAULTS["nvidia"]["base_url"])
        self.assertEqual(cfg.api_key, "k-nvidia")

    def test_build_loop_registry_hit(self):
        from harness.config import Config
        cfg = Config(provider="fast", model="top",
                     base_url="http://stale/v1",
                     api_key_env="OPENAI_API_KEY", api_key="k-openai",
                     providers={"fast": {
                         "kind": "nvidia",
                         "base_url":
                         "https://integrate.api.nvidia.com/v1",
                         "model": "nemotron",
                         "api_key_env": "NVIDIA_API_KEY"}})
        loop = cli._build_loop(cfg, make_args(provider="fast"))
        self.assertEqual(cfg.base_url,
                         "https://integrate.api.nvidia.com/v1")
        self.assertEqual(cfg.model, "nemotron")
        self.assertEqual(loop.provider.model, "nemotron")

    def test_providers_lines_registry_and_legacy(self):
        self.assertEqual(cli.providers_lines()[0], "[providers]")
        cfg = make_cfg(provider="fast", providers={"fast": {
            "kind": "openai", "base_url": "http://x/v1",
            "model": "m", "api_key_env": None}})
        lines = cli.providers_lines(
            cfg, _probe=lambda base, kind: True)
        self.assertEqual(lines[0], "[providers]")
        self.assertIn("● fast *", lines[1])
        self.assertIn("kind=openai", lines[1])
        self.assertIn("model=m", lines[1])
        lines = cli.providers_lines(
            cfg, _probe=lambda base, kind: None)
        self.assertIn("· fast *", lines[1])

    def test_save_current(self):
        cfg = make_cfg()
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.json"
            target.write_text(json.dumps({"provider": "openai"}))
            lines = save_current_provider(cfg, "snap", path=target)
            self.assertIn("saved 'snap'", lines[0])
            raw = json.loads(target.read_text())
            self.assertEqual(raw["providers"]["snap"]["model"], "gpt-5")
            self.assertEqual(raw["provider"], "openai")  # not activated
            self.assertIn("already exists",
                          save_current_provider(cfg, "snap",
                                                path=target)[0])


class TestRegistryPickerHelpers(unittest.TestCase):
    def test_format_and_rows(self):
        from harness.tui.app import (format_provider_row,
                                     format_registry_row,
                                     prefill_from_template,
                                     provider_entry_rows,
                                     validate_new_provider)
        # Legacy two-arg shape unchanged.
        self.assertEqual(format_provider_row("openai", "openai"), "* openai")
        cfg = make_cfg(provider="b",
                       providers={"b": dict(LOCAL), "a": dict(LOCAL)})
        rows = provider_entry_rows(cfg)
        self.assertEqual([r["name"] for r in rows], ["a", "b"])
        self.assertTrue(rows[1]["active"])
        line = format_registry_row(rows[1], "b")
        self.assertIn("*", line.split("b")[0])
        self.assertIn("openai/Qwen", line)
        # Validation helper returns strings, None when ok.
        self.assertIsNone(validate_new_provider("n", dict(LOCAL), {}))
        self.assertIn("already exists", validate_new_provider(
            "n", dict(LOCAL), {"n": {} }) or "")
        self.assertIn("non-empty", validate_new_provider(" ", dict(LOCAL),
                                                         {}) or "")
        pre = prefill_from_template("llama.cpp")
        self.assertEqual(pre["kind"], "openai")
        self.assertIn("127.0.0.1", pre["base_url"])
        blank = prefill_from_template(None)
        self.assertEqual(blank["kind"], "openai")


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestRegistryPilot(unittest.IsolatedAsyncioTestCase):
    async def _app(self, d, control, cfg):
        from harness.loop import Session
        from harness.tui.app import HarnessApp
        from harness.tui.bridge import TuiBridge
        sdir = Path(d) / "s1"
        sdir.mkdir(exist_ok=True)
        sess = Session(id="s1", dir=sdir, workdir=d)
        loop = SimpleNamespace(approver=None)
        return HarnessApp(loop, sess, TuiBridge(),
                          provider_name=cfg.provider, model=cfg.model,
                          cfg=cfg, control=control)

    async def test_provider_add_flow(self):
        from harness.tui.app import (ProviderAddScreen, ProviderScreen,
                                     ProviderTemplateScreen)
        cfg = make_cfg(providers={"b": dict(LOCAL)})
        control = SimpleNamespace(
            do_retarget=lambda p, m: [f"{p}/{m}"],
            sync_state=lambda: (control.loop, control.sess),
            get_provider_model=lambda: ("b", "Qwen"),
            loop=SimpleNamespace(approver=None), sess=None)
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control, cfg)
            control.sess = app._session
            async with app.run_test() as pilot:
                app._open_provider_picker()
                await pilot.pause()
                self.assertIsInstance(app.screen, ProviderScreen)
                app.screen.choose("__add__")
                await pilot.pause()
                self.assertIsInstance(app.screen, ProviderTemplateScreen)
                app.screen.choose("llama.cpp")
                await pilot.pause()
                self.assertIsInstance(app.screen, ProviderAddScreen)
                # Prefilled from the template; validation is inline.
                data = app.screen._data()
                self.assertIn("127.0.0.1", data["base_url"])
                self.assertEqual(
                    app.screen._data()["name"], "llama.cpp")
                app.pop_screen()
                app.pop_screen()


if __name__ == "__main__":
    unittest.main()
