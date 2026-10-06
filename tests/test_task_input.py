"""TaskInput widget: ctrl+enter submits, up/down recalls history,
multi-line input keeps cursor movement. Needs the textual extra."""
import unittest

from harness.tui import has_tui


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestTaskInput(unittest.IsolatedAsyncioTestCase):
    async def _app(self):
        from textual.app import App, ComposeResult
        from harness.tui.widgets import TaskInput

        submitted = []

        class TA(App):
            def compose(self) -> ComposeResult:
                yield TaskInput(id="ti", on_submit=submitted.append)

        app = TA()
        return app, submitted

    async def test_submit_and_history(self):
        from harness.tui.widgets import TaskInput

        app, submitted = await self._app()
        async with app.run_test() as pilot:
            ti = app.query_one("#ti", TaskInput)
            ti.focus()
            await pilot.press(*"hi")
            await pilot.press("ctrl+enter")
            self.assertEqual(submitted, ["hi"])
            self.assertEqual(ti.text, "")

            await pilot.press(*"there")
            await pilot.press("ctrl+enter")
            self.assertEqual(submitted, ["hi", "there"])

            await pilot.press("up")
            self.assertEqual(ti.text, "there")
            await pilot.press("up")
            self.assertEqual(ti.text, "hi")
            await pilot.press("down")
            self.assertEqual(ti.text, "there")
            await pilot.press("down")
            self.assertEqual(ti.text, "")

    async def test_blank_submit_ignored(self):
        from harness.tui.widgets import TaskInput

        app, submitted = await self._app()
        async with app.run_test() as pilot:
            ti = app.query_one("#ti", TaskInput)
            ti.focus()
            await pilot.press("space", "ctrl+enter")
            self.assertEqual(submitted, [])
            self.assertEqual(len(ti.input_history), 0)

    async def test_multiline_keeps_cursor_movement(self):
        from harness.tui.widgets import TaskInput

        app, submitted = await self._app()
        async with app.run_test() as pilot:
            ti = app.query_one("#ti", TaskInput)
            ti.focus()
            ti.text = "line1\nline2"
            row_before = ti.cursor_location[0]
            await pilot.press("up")
            # multiline: up moves the cursor, does NOT recall history
            self.assertEqual(ti.text, "line1\nline2")
            self.assertLess(ti.cursor_location[0], row_before + 1)
            self.assertEqual(submitted, [])

    async def test_ctrl_enter_submits_multiline(self):
        from harness.tui.widgets import TaskInput

        app, submitted = await self._app()
        async with app.run_test() as pilot:
            ti = app.query_one("#ti", TaskInput)
            ti.focus()
            await pilot.press(*"line1")
            await pilot.press("enter")
            await pilot.press(*"line2")
            self.assertIn("\n", ti.text)
            await pilot.press("ctrl+enter")
            self.assertEqual(submitted, ["line1\nline2"])
            self.assertEqual(ti.text, "")


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestInterruptKey(unittest.IsolatedAsyncioTestCase):
    """Escape on the main screen asks the loop to stop the running turn."""

    async def _app(self, loop):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from harness.loop import Session
        from harness.tui.app import HarnessApp
        from harness.tui.bridge import TuiBridge

        d = tempfile.mkdtemp()
        sdir = Path(d) / "s1"
        sdir.mkdir(exist_ok=True)
        sess = Session(id="s1", dir=sdir, workdir=d)
        control = SimpleNamespace(
            do_open=lambda sid: [], do_new=lambda rest="": [],
            sync_state=lambda: (loop, sess),
            sessions_info=lambda: [],
            get_provider_model=lambda: ("openai", "m"))
        return HarnessApp(loop, sess, TuiBridge(),
                          provider_name="openai", model="m", control=control)

    async def test_escape_interrupts_running_turn(self):
        import time
        from types import SimpleNamespace

        calls = []
        loop = SimpleNamespace(
            approver=None, request_stop=lambda: calls.append("stop"))
        app = await self._app(loop)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._turn_start = time.monotonic()  # simulate a running turn
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(calls, ["stop"])
            self.assertTrue(any("[interrupt requested]" in l
                                for l in app._transcript_lines))

    async def test_escape_idle_is_noop(self):
        from types import SimpleNamespace

        calls = []
        loop = SimpleNamespace(
            approver=None, request_stop=lambda: calls.append("stop"))
        app = await self._app(loop)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIsNone(app._turn_start)
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
