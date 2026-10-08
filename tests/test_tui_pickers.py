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
        # retarget_loop persists the selection: redirect the config path
        # to temp so tests never rewrite the developer's real config.
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg_patch = mock.patch.object(
            cli, "config_path",
            return_value=Path(self._tmp.name) / "config.json")
        self._cfg_patch.start()

    def tearDown(self):
        self._cfg_patch.stop()
        self._tmp.cleanup()
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


class TestRestoreSessionModel(unittest.TestCase):
    """_restore_session_model reports whether it retargeted (callers
    repaint the header only then; attach() paints pre-restore)."""

    def setUp(self):
        self._saved = dict(os.environ)
        os.environ["OPENAI_API_KEY"] = "k-openai"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _session_with_meta(self, meta):
        import json
        from harness.loop import Session
        d = tempfile.mkdtemp()
        sdir = Path(d) / "s1"
        sdir.mkdir(exist_ok=True)
        if meta is not None:
            (sdir / "meta.json").write_text(json.dumps(meta))
        return Session(id="s1", dir=sdir, workdir=d)

    def _patched(self):
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        return (mock.patch.object(
            cli, "config_path",
            return_value=Path(tmp.name) / "config.json"),
            mock.patch.object(cli, "detect_context_window",
                              return_value=None),
            tmp)

    def test_retargets_and_reports_true(self):
        cfg = make_cfg(provider="openai", model="gpt-5",
                       base_url="http://localhost:1234/v1",
                       api_key="k-openai")
        box = {"session": self._session_with_meta(
            {"provider": "openai", "model": "m2"}),
            "loop": make_box(cfg)["loop"]}
        cfg_patch, det_patch, tmp = self._patched()
        with cfg_patch, det_patch:
            self.assertTrue(
                cli._restore_session_model(box, cfg, make_args()))
        tmp.cleanup()
        self.assertEqual((cfg.provider, cfg.model), ("openai", "m2"))

    def test_noop_when_current_reports_false(self):
        cfg = make_cfg(provider="openai", model="gpt-5",
                       base_url="http://localhost:1234/v1",
                       api_key="k-openai")
        box = {"session": self._session_with_meta(
            {"provider": "openai", "model": "gpt-5"}),
            "loop": make_box(cfg)["loop"]}
        cfg_patch, det_patch, tmp = self._patched()
        with cfg_patch, det_patch:
            self.assertFalse(
                cli._restore_session_model(box, cfg, make_args()))
        tmp.cleanup()
        self.assertEqual((cfg.provider, cfg.model), ("openai", "gpt-5"))

    def test_noop_without_meta_reports_false(self):
        cfg = make_cfg(provider="openai", model="gpt-5",
                       base_url="http://localhost:1234/v1",
                       api_key="k-openai")
        box = {"session": self._session_with_meta(None),
               "loop": make_box(cfg)["loop"]}
        cfg_patch, det_patch, tmp = self._patched()
        with cfg_patch, det_patch:
            self.assertFalse(
                cli._restore_session_model(box, cfg, make_args()))
        tmp.cleanup()


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

    async def test_export_transcript_writes_file(self):
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                app._log("hello-copy")
                app._log("world-copy")
                app._export_transcript()
                await pilot.pause()
                files = sorted(app._session.dir.glob("transcript-*.log"))
                self.assertTrue(files, "no transcript export written")
                text = files[-1].read_text(encoding="utf-8")
                self.assertIn("hello-copy", text)
                self.assertIn("world-copy", text)

    async def test_add_screen_has_save_and_back(self):
        from harness.tui.app import ProviderAddScreen
        from textual.widgets import Button
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                app.push_screen(ProviderAddScreen(prefill="llama.cpp"))
                await pilot.pause()
                self.assertIsInstance(app.screen, ProviderAddScreen)
                self.assertIsInstance(
                    app.screen.query_one("#add-save", Button), Button)
                self.assertIsInstance(
                    app.screen.query_one("#add-back", Button), Button)
                # Back closes without touching disk.
                app.screen.action_close()
                await pilot.pause()
                self.assertNotIsInstance(app.screen, ProviderAddScreen)

    async def test_provider_kinds_listed_with_entries(self):
        # Regression: legacy kinds (nvidia included) must stay visible
        # even when the registry is non-empty.
        from harness.tui.app import ProviderScreen
        scr = ProviderScreen(
            current="llama",
            entries=[{"name": "llama", "kind": "openai",
                      "model": "Qwen",
                      "base_url": "http://127.0.0.1:8080/v1",
                      "dot": True}])
        ids = [o.id for o in scr._row_options()]
        self.assertIn("llama", ids)
        self.assertIn("nvidia", ids)

    async def test_add_screen_esc_closes_from_input(self):
        from harness.tui.app import ProviderAddScreen
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                app.push_screen(ProviderAddScreen(prefill="llama.cpp"))
                await pilot.pause()
                self.assertIsInstance(app.screen, ProviderAddScreen)
                app.screen.query_one("#add-name").focus()
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, ProviderAddScreen)

    async def test_add_screen_save_flow(self):
        import json
        from unittest import mock
        from harness.tui.app import ProviderAddScreen
        from textual.widgets import Input
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
                "retarget", ("t1", "m")),
            providers={}, loop=SimpleNamespace(approver=None),
            sess=None)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "config.json"
            target.write_text(json.dumps({"provider": "openai"}))
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                app.push_screen(ProviderAddScreen(prefill="llama.cpp"))
                await pilot.pause()
                scr = app.screen
                self.assertIsInstance(scr, ProviderAddScreen)
                scr.query_one("#add-name", Input).value = "t1"
                scr.query_one("#add-model", Input).value = "m"
                with mock.patch("harness.config.config_path",
                               return_value=target):
                    scr._submit()
                await pilot.pause()
                raw = json.loads(target.read_text(encoding="utf-8"))
                self.assertIn("t1", raw.get("providers", {}))
                self.assertEqual(raw.get("provider"), "t1")
                self.assertEqual(calls.get("retarget"), ("t1", "m"))

    async def test_help_lists_tui_keys(self):
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test():
                self.assertTrue(app._handle_slash("/help"))
                text = "\n".join(app._transcript_lines)
                self.assertIn("[tui keys]", text)
                self.assertIn("ctrl+c", text)

    async def test_focus_returns_to_input_after_dialog(self):
        from harness.tui.widgets import TaskInput
        from harness.tui.app import SessionPickerScreen
        control = SimpleNamespace(
            do_open=lambda sid: [f"opened {sid}"],
            do_new=lambda rest="": ["new session"],
            sync_state=lambda: (control.loop, control.sess),
            sessions_info=lambda: [
                {"id": "b", "current": False, "project": None,
                 "tokens": None}],
            get_provider_model=lambda: ("openai", "m"),
            loop=SimpleNamespace(approver=None), sess=None)
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsInstance(app.screen.focused, TaskInput)
                app._open_session_picker()
                await pilot.pause()
                self.assertIsInstance(app.screen, SessionPickerScreen)
                await pilot.press("escape")
                await pilot.pause()
                await pilot.pause()
                self.assertNotIsInstance(app.screen,
                                         SessionPickerScreen)
                self.assertIsInstance(app.screen.focused, TaskInput)

    async def test_transcript_click_to_copy(self):
        # Per-message widgets replaced the single Log: clicking a message
        # copies its full plain (ANSI-stripped) text via
        # app.copy_to_clipboard. Rob approved this trade explicitly.
        from harness.tui.widgets import MessageWidget, TranscriptContainer
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test(size=(80, 30)) as pilot:
                tc = app.query_one("#transcript", TranscriptContainer)
                app._log("alpha \x1b[1mbold\x1b[0m line", kind="assistant")
                app._log("user line", kind="user")
                await pilot.pause()
                widgets = list(tc.query(MessageWidget))
                self.assertEqual(len(widgets), 2)
                # CSS classes mark the kinds
                self.assertIn("message-assistant", widgets[0].classes)
                self.assertIn("message-user", widgets[1].classes)
                # plain text stored without ANSI
                self.assertEqual(widgets[0].plain_text, "alpha bold line")
                # click copies the message text
                copied = {}
                app.copy_to_clipboard = lambda t: copied.setdefault("t", t)
                await pilot.click(MessageWidget)
                await pilot.pause()
                self.assertEqual(copied.get("t"), "alpha bold line")
                # export lines still recorded for the file export path
                self.assertEqual(len(app._transcript_lines), 2)

    async def test_transcript_message_widgets_wrap(self):
        # Per-message widgets wrap natively (Static wraps); long lines
        # must not overflow the pane horizontally.
        from harness.tui.widgets import MessageWidget, TranscriptContainer
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test(size=(60, 20)) as pilot:
                tc = app.query_one("#transcript", TranscriptContainer)
                app._log("word " * 60, kind="assistant")  # 300 chars
                app._log("x" * 400, kind="tool")  # long single word
                await pilot.pause()
                widgets = list(tc.query(MessageWidget))
                self.assertEqual(len(widgets), 2)
                # No horizontal overflow from the container.
                self.assertLessEqual(tc.virtual_size.width, tc.size.width)
                # Narrow the terminal: still no overflow.
                await pilot.resize_terminal(40, 20)
                await pilot.pause()
                self.assertLessEqual(tc.virtual_size.width, tc.size.width)
                # Widen again: messages persist.
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                self.assertLessEqual(tc.virtual_size.width, tc.size.width)
                self.assertEqual(len(list(tc.query(MessageWidget))), 2)

    async def test_request_meter_is_bar_not_transcript(self):
        # The [context ...] meter line is CLI furniture; in the TUI the
        # budget bar owns those numbers, so the transcript must not get
        # a meter line per model call.
        from harness.tui.widgets import BudgetBar
        control = SimpleNamespace()
        with tempfile.TemporaryDirectory() as d:
            app = await self._app(d, control)
            control.sess = app._session
            async with app.run_test(size=(80, 30)) as pilot:
                req = {"tokens_est": 80168, "hard": 100000,
                       "soft": 80000, "status": "warn",
                       "breakdown": {"system": 568,
                                     "transcript": 79090,
                                     "tools": 510, "total": 80168},
                       "usage_total": {"input": 385656,
                                       "output": 8383}}
                app._bridge("request", req)
                app._poll()
                bar = app.query_one("#budget", BudgetBar)
                self.assertIn("80,168", str(bar.render()))
                self.assertIn("385,656", str(bar.render()))
                self.assertIn("warn", bar.classes)
                gauge = app.query_one("#gauge")
                gtext = str(gauge.render())
                # Gauge shows LCARS bar with pct, or thinking animation if active
                # (both valid — animation takes over during turns)
                self.assertTrue("80%" in gtext or "▁" in gtext or "█" in gtext,
                                f"gauge should show budget or animation, got: {gtext[:60]!r}")
                self.assertIn("warn", gauge.classes)
                joined = "\n".join(app._transcript_lines)
                self.assertNotIn("[context", joined)
                # Budget warn/over is agent-facing; gauge owns the
                # colour state, so the transcript stays clean of it.
                app._bridge("budget", {"status": "warn", "tokens": 91630,
                                       "breakdown": {"total": 91630}})
                app._bridge("prune", {"attempt": 1, "tokens": 91630})
                app._poll()
                joined = "\n".join(app._transcript_lines)
                self.assertNotIn("WARN budget", joined)
                self.assertNotIn("[WARN", joined)
                # System kinds (budget, prune) go to the top panel,
                # not the chat transcript.
                self.assertNotIn("[prune-only turn 1", joined)
                sys_panel = app.query_one("#system")
                sys_text = "\n".join(str(l) for l in sys_panel.lines)
                self.assertIn("[prune-only turn 1", sys_text)
                self.assertIn("WARN budget", sys_text)
                # Real transcript content still flows through.
                app._bridge("assistant", "hello from the model")
                app._bridge("response", {"content": "hello from the model"})
                app._poll()
                joined = "\n".join(app._transcript_lines)
                self.assertIn("hello from the model", joined)

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
