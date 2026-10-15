"""Pilot test: per-message navigation + copy keybinding (opencode behaviors).

Drives the real HarnessApp. Asserts:
- alt+j / alt+k move a cursor through message widgets (jump-to-message)
- alt+u jumps to the last user message
- alt+c copies the current (or last) message's plain text
- navigating pins the transcript; new messages don't yank the view
Red-first: fails until the bindings/actions exist.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.tui import has_tui


def _fake_session(d):
    sdir = Path(d) / "sess"
    sdir.mkdir()
    return SimpleNamespace(id="t", dir=sdir, workdir=d)


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestMessageNav(unittest.IsolatedAsyncioTestCase):
    async def _run_with_messages(self, msgs, body):
        from harness.tui.app import HarnessApp
        from harness.tui.widgets import TranscriptContainer
        from harness.tui.bridge import TuiBridge
        with tempfile.TemporaryDirectory() as d:
            sess = _fake_session(d)
            loop = SimpleNamespace(approver=SimpleNamespace(
                decide=None, auto_approve=False))
            app = HarnessApp(loop, sess, TuiBridge())
            async with app.run_test() as pilot:
                box = app.query_one("#transcript", TranscriptContainer)
                for kind, text in msgs:
                    box.write_message(kind, text)
                    await pilot.pause()
                await body(app, pilot, box)

    def test_bindings_registered(self):
        from harness.tui.app import HarnessApp

        def _key(b):
            return b.key if hasattr(b, "key") else b[0]

        keys = [_key(b) for b in HarnessApp.BINDINGS]
        for key in ("alt+j", "alt+k", "alt+u", "alt+c"):
            self.assertIn(key, keys, f"missing binding {key}")

    async def test_nav_next_prev_and_copy(self):
        msgs = [("user", "hello"), ("assistant", "world"),
                ("tool", "ls output")]

        async def body(app, pilot, box):
            self.assertEqual(box.message_count, 3)

            # No nav yet: copy grabs the last message.
            copied = []
            app.copy_to_clipboard = copied.append
            app.action_copy_message()
            self.assertEqual(copied, ["ls output"])

            # alt+k steps forward through messages.
            await pilot.press("alt+k")
            await pilot.pause()
            self.assertEqual(box.nav_index, 0)
            cur = box.current_message()
            self.assertIsNotNone(cur)
            self.assertEqual(cur.plain_text, "hello")
            self.assertIn("message-current", cur.classes)

            await pilot.press("alt+k")
            await pilot.pause()
            self.assertEqual(box.current_message().plain_text, "world")

            # alt+j steps back.
            await pilot.press("alt+j")
            await pilot.pause()
            self.assertEqual(box.current_message().plain_text, "hello")

            # alt+j at the top: stays, no crash.
            await pilot.press("alt+j")
            await pilot.pause()
            self.assertEqual(box.nav_index, 0)

            # alt+c copies the navigated-to message.
            copied.clear()
            await pilot.press("alt+c")
            await pilot.pause()
            self.assertEqual(copied, ["hello"])

        await self._run_with_messages(msgs, body)

    async def test_nav_past_end_resumes_follow(self):
        async def body(app, pilot, box):
            await pilot.press("alt+k")
            await pilot.pause()
            await pilot.press("alt+k")
            await pilot.pause()
            self.assertEqual(box.nav_index, 1)
            # One more step: past the end -> follow mode, no current.
            await pilot.press("alt+k")
            await pilot.pause()
            self.assertIsNone(box.nav_index)
            self.assertIsNone(box.current_message())

        await self._run_with_messages(
            [("user", "one"), ("assistant", "two")], body)

    async def test_jump_to_last_user_message(self):
        async def body(app, pilot, box):
            await pilot.press("alt+u")
            await pilot.pause()
            cur = box.current_message()
            self.assertIsNotNone(cur)
            self.assertEqual(cur.plain_text, "q2")
            self.assertEqual(cur.message_kind, "user")

        await self._run_with_messages(
            [("user", "q1"), ("assistant", "a1"),
             ("user", "q2"), ("assistant", "a2")], body)

    async def test_nav_pins_view_on_new_message(self):
        async def body(app, pilot, box):
            await pilot.press("alt+k")
            await pilot.pause()
            self.assertEqual(box.nav_index, 0)
            # New message while navigating: nav stays put (view not yanked).
            box.write_message("assistant", "a2")
            await pilot.pause()
            self.assertEqual(box.nav_index, 0)
            self.assertEqual(box.current_message().plain_text, "q1")
            self.assertEqual(box.message_count, 3)

        await self._run_with_messages(
            [("user", "q1"), ("assistant", "a1")], body)

    async def test_copy_with_no_messages_is_noop(self):
        async def body(app, pilot, box):
            copied = []
            app.copy_to_clipboard = copied.append
            app.action_copy_message()  # must not raise
            self.assertEqual(copied, [])

        await self._run_with_messages([], body)
