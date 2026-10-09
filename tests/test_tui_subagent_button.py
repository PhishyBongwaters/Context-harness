"""TUI: Subagent footer button + inspection screen (headless)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from harness.tui.app import HarnessApp


def _app_with_subagent(tmp=None):
    # HarnessApp needs (loop, session, bridge); use mocks.
    app = HarnessApp(MagicMock(), MagicMock(), MagicMock())
    # fake agent loop with a subagent slot
    sdir = Path(tmp or tempfile.mkdtemp()) / "subagents" / "abc123"
    sdir.mkdir(parents=True)
    (sdir / "history.md").write_text("## turn 1\nhello from sub\n",
                                     encoding="utf-8")
    (sdir / "result.md").write_text("# Subagent result\n- status: completed\n",
                                    encoding="utf-8")
    thread = MagicMock()
    thread.is_alive.return_value = False
    loop = MagicMock()
    loop._subagent = {"sdir": sdir, "task": "do the thing",
                      "thread": thread,
                      "result_path": str(sdir / "result.md")}
    app._agent_loop = loop
    return app, sdir


class TestSubagentButtonBinding(unittest.TestCase):
    def test_binding_exists(self):
        def _key(b):
            return b.key if hasattr(b, "key") else b[0]

        def _action(b):
            return b.action if hasattr(b, "action") else b[1]

        keys = [_key(b) for b in HarnessApp.BINDINGS]
        self.assertIn("ctrl+g", keys)
        actions = [_action(b) for b in HarnessApp.BINDINGS]
        self.assertIn("view_subagent", actions)


class TestSubagentButton(unittest.IsolatedAsyncioTestCase):
    async def test_action_opens_screen(self):
        app, sdir = _app_with_subagent()
        async with app.run_test() as pilot:
            app.action_view_subagent()
            await pilot.pause()
            # the modal screen is on the stack
            self.assertEqual(len(app.screen_stack), 2)
            screen = app.screen
            self.assertIn("SubagentScreen",
                          type(screen).__name__)
            # log contains the subagent's files
            log = screen.query_one("#sub-log")
            text = log.get_content() if hasattr(log, "get_content") else ""
            # Log widget: read lines via its internal render
            rendered = "\n".join(
                getattr(line, "text", str(line))
                for line in getattr(log, "_lines", []))
            combined = text + rendered
            self.assertIn("hello from sub", combined)
            # escape closes
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(len(app.screen_stack), 1)


if __name__ == "__main__":
    unittest.main()
