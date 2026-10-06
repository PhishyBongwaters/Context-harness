"""TUI pickers: sessions_info + retarget_loop + pilot screens.

Stdlib-only except the pilot, which skips cleanly without the extra.
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness import __main__ as cli
from harness.config import PROVIDER_DEFAULTS
from harness.tui import has_tui
from harness.tui import commands


def make_cfg(**kw):
    base = {"provider": "openai", "model": "gpt-5",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY", "api_key": "k-openai",
            "prune_provider": None, "prune_model": None,
            "prune_base_url": None, "prune_api_key_env": None,
            "request_timeout": 120}
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


class TestSessionsInfo(unittest.TestCase):
    def test_newest_first_and_current(self):
        with tempfile.TemporaryDirectory() as d:
            for sid in ("20261005-080000", "20261005-090000"):
                (Path(d) / sid).mkdir()
            (Path(d) / ".current").write_text("20261005-090000")
            cfg = SimpleNamespace(sessions_path=Path(d))
            rows = cli.sessions_info(cfg)
            self.assertEqual([r["id"] for r in rows],
                             ["20261005-090000", "20261005-080000"])
            cur = [r for r in rows if r["current"]]
            self.assertEqual(len(cur), 1)
            self.assertEqual(cur[0]["id"], "20261005-090000")

    def test_project_and_tokens_best_effort(self):
        with tempfile.TemporaryDirectory() as d:
            sdir = Path(d) / "20261005-090000"
            sdir.mkdir()
            (sdir / "project").write_text("proj-a\n")
            (sdir / "context.md").write_text("## user\nhello\n")
            (Path(d) / "20261005-080000").mkdir()  # no context/project
            (Path(d) / ".current").write_text("20261005-090000")
            cfg = SimpleNamespace(sessions_path=Path(d))
            rows = {r["id"]: r for r in cli.sessions_info(cfg)}
            self.assertEqual(rows["20261005-090000"]["project"], "proj-a")
            self.assertIsInstance(rows["20261005-090000"]["tokens"], int)
            self.assertIsNone(rows["20261005-080000"]["project"])
            self.assertIsNone(rows["20261005-080000"]["tokens"])

    def test_sessions_lines_parity(self):
        # Byte-identical to the old sorted-ascending "id *" format.
        with tempfile.TemporaryDirectory() as d:
            for sid in ("20261005-080000", "20261005-090000"):
                (Path(d) / sid).mkdir()
            (Path(d) / ".current").write_text("20261005-090000")
            cfg = SimpleNamespace(sessions_path=Path(d))
            cur = "20261005-090000"
            expected = [f"{sid}{' *' if sid == cur else ''}"
                        for sid in sorted(("20261005-080000",
                                           "20261005-090000"))]
            self.assertEqual(cli.sessions_lines(cfg), expected)


class TestRetargetLoop(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "k-openai"
        os.environ["ANTHROPIC_API_KEY"] = "k-anthropic"
        os.environ["NVIDIA_API_KEY"] = "k-nvidia"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_base_url_reset_and_key_reload(self):
        cfg = make_cfg(provider="openai", model="gpt-5",
                       base_url="http://localhost:1234/v1",
                       api_key="k-openai")
        box = make_box(cfg)
        cli.retarget_loop(box, cfg, make_args(), "anthropic", "claude-x")
        self.assertEqual(cfg.provider, "anthropic")
        self.assertEqual(
            cfg.base_url,
            PROVIDER_DEFAULTS["anthropic"]["base_url"])
        self.assertEqual(cfg.api_key_env, "ANTHROPIC_API_KEY")
        self.assertEqual(cfg.api_key, "k-anthropic")
        self.assertEqual(cfg.model, "claude-x")
        self.assertEqual(box["loop"].provider.model, "claude-x")
        # prune_* unset -> falls back to main.
        self.assertEqual(box["loop"].prune_provider.model, "claude-x")

    def test_explicit_base_url_kept(self):
        cfg = make_cfg(base_url="http://local:8080/v1")
        box = make_box(cfg)
        args = make_args(base_url="http://local:8080/v1")
        cli.retarget_loop(box, cfg, args, "openai", "gpt-9")
        self.assertEqual(cfg.base_url, "http://local:8080/v1")
        self.assertEqual(cfg.model, "gpt-9")

    def test_missing_key_raises_and_keeps_old(self):
        del os.environ["ANTHROPIC_API_KEY"]
        cfg = make_cfg(provider="openai", model="gpt-5",
                       base_url=PROVIDER_DEFAULTS["openai"]["base_url"],
                       api_key="k-openai")
        box = make_box(cfg)
        old_provider = box["loop"].provider
        with self.assertRaises(SystemExit):
            cli.retarget_loop(box, cfg, make_args(),
                              "anthropic", "claude-x")
        self.assertEqual(cfg.provider, "openai")
        self.assertEqual(cfg.model, "gpt-5")
        self.assertIs(box["loop"].provider, old_provider)

    def test_unknown_command_hints(self):
        hint = commands.unknown_hint("bogus")
        self.assertEqual(hint, "Unknown command /bogus (/help).")
        self.assertEqual(commands.local_lines("bogus", ""), [hint])


class TestPickerHelpers(unittest.TestCase):
    def test_format_and_parse(self):
        from harness.tui.app import (format_provider_row, format_session_row,
                                     parse_model_ids)
        row = {"id": "s1", "current": True, "project": "p",
               "tokens": 1234}
        line = format_session_row(row)
        self.assertIn("* s1", line)
        self.assertIn("[p]", line)
        self.assertIn("1,234 tokens", line)
        row2 = {"id": "s2", "current": False, "project": None,
                "tokens": None}
        self.assertNotIn("*", format_session_row(row2).split("s2")[0])
        self.assertEqual(format_provider_row("openai", "openai"),
                         "* openai")
        self.assertEqual(parse_model_ids(["[models] 2 models from x",
                                          "  m1", "  m2",
                                          "  ... +1 more"]),
                         ["m1", "m2"])
        self.assertEqual(parse_model_ids(
            ["[models] error fetching models: boom"]), [])


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestPickerPilot(unittest.IsolatedAsyncioTestCase):
    async def _app(self, d, control, sess_id="s1"):
        from harness.loop import Session
        from harness.tui.app import HarnessApp
        from harness.tui.bridge import TuiBridge
        sdir = Path(d) / sess_id
        sdir.mkdir(exist_ok=True)
        sess = Session(id=sess_id, dir=sdir, workdir=d)
        loop = SimpleNamespace(approver=None)
        bridge = TuiBridge()
        app = HarnessApp(loop, sess, bridge, provider_name="openai",
                         model="m",
                         cfg=SimpleNamespace(provider="openai", model="m"),
                         control=control)
        return app

    async def test_session_screen_select_calls_sync(self):
        from harness.tui.app import SessionPickerScreen
        calls = {}

        def do_open(sid):
            calls["open"] = sid
            return [f"opened {sid}"]

        def do_new(rest=""):
            calls["new"] = rest
            return ["new session"]

        def sync():
            calls["sync"] = calls.get("sync", 0) + 1
            return (control.loop, control.sess)

        control = SimpleNamespace(
            do_open=do_open, do_new=do_new, sync_state=sync,
            sessions_info=lambda: [
                {"id": "b", "current": False, "project": None,
                 "tokens": None},
                {"id": "a", "current": True, "project": None,
                 "tokens": None}],
            get_provider_model=lambda: ("openai", "m"),
            loop=SimpleNamespace(approver=None), sess=None)
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                app._open_session_picker()
                await pilot.pause()
                self.assertIsInstance(app.screen, SessionPickerScreen)
                app.screen.choose("b")
                await pilot.pause()
                self.assertEqual(calls.get("open"), "b")
                self.assertGreaterEqual(calls.get("sync", 0), 1)

    async def test_provider_confirm_calls_retarget(self):
        calls = {}

        def do_retarget(provider, model):
            calls["retarget"] = (provider, model)
            return [f"{provider}/{model}"]

        def sync():
            calls["sync"] = calls.get("sync", 0) + 1
            return (control.loop, control.sess)

        control = SimpleNamespace(
            do_retarget=do_retarget, sync_state=sync,
            get_provider_model=lambda: calls.get(
                "retarget", ("openai", "m")),
            loop=SimpleNamespace(approver=None), sess=None)
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                ok = app._retarget_provider_model("anthropic", "claude-x")
                await pilot.pause()
                self.assertTrue(ok)
                self.assertEqual(calls.get("retarget"),
                                 ("anthropic", "claude-x"))
                self.assertGreaterEqual(calls.get("sync", 0), 1)
                # Missing-key path keeps old, reports, no sync growth.
                before = calls.get("sync", 0)
                control.do_retarget = lambda p, m: (_ for _ in ()).throw(
                    SystemExit("No API key for main model"))
                ok = app._retarget_provider_model("anthropic", "claude-x")
                self.assertFalse(ok)
                self.assertEqual(calls.get("sync", 0), before)


if __name__ == "__main__":
    unittest.main()
