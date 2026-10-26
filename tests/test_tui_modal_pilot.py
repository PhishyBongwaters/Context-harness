"""Pilot test: end-to-end approval through the real Textual app.

Drives HarnessApp with a fake agent loop whose run_turn triggers a real
TUIApprover.resolve on a mutating exec. Asserts the modal pops and that
a/s/d keys resolve the turn. Skipped when the textual extra is missing.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.loop import Session
from harness.tui import has_tui
from harness.tui.approvals import TUIApprover
from harness.tui.bridge import TuiBridge


class FakeLoop:
    def __init__(self, approver):
        self.approver = approver
        self.results: list = []

    def run_turn(self, session, text):
        from harness.approvals import Policy
        pol = Policy(session.workdir, session.dir,
                     session.context.path)
        ok, denial = self.approver.resolve(
            pol, "exec", {"command": "touch mutate-me"})
        self.results.append((ok, denial))
        return "done"


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestModalPilot(unittest.IsolatedAsyncioTestCase):
    async def _modal_screen(self, app, pilot):
        from harness.tui.app import ApprovalScreen
        for _ in range(200):
            if isinstance(app.screen, ApprovalScreen):
                return app.screen
            await pilot.pause()
        self.fail("approval modal never appeared; "
                  f"screen={type(app.screen).__name__}")

    async def test_session_approve_via_modal(self):
        from harness.tui.app import ApprovalScreen, HarnessApp
        with tempfile.TemporaryDirectory() as d:
            sdir = Path(d) / "sess"
            sdir.mkdir()
            sess = Session(id="t", dir=sdir, workdir=d)
            approver = TUIApprover(sdir, approval_timeout=30)
            loop = FakeLoop(approver)
            bridge = TuiBridge()
            app = HarnessApp(loop, sess, bridge)
            async with app.run_test() as pilot:
                app._submit("do it")
                scr = await self._modal_screen(app, pilot)
                self.assertIsInstance(scr, ApprovalScreen)
                await pilot.press("s")
                for _ in range(200):
                    if loop.results:
                        break
                    await pilot.pause()
                self.assertTrue(loop.results, "turn never finished")
                ok, _denial = loop.results[0]
                self.assertTrue(ok)
                self.assertEqual(len(approver.session_keys), 1)

    async def test_deny_via_modal(self):
        from harness.tui.app import HarnessApp
        with tempfile.TemporaryDirectory() as d:
            sdir = Path(d) / "sess"
            sdir.mkdir()
            sess = Session(id="t", dir=sdir, workdir=d)
            approver = TUIApprover(sdir, approval_timeout=30)
            loop = FakeLoop(approver)
            bridge = TuiBridge()
            app = HarnessApp(loop, sess, bridge)
            async with app.run_test() as pilot:
                app._submit("do it")
                await self._modal_screen(app, pilot)
                await pilot.press("d")
                for _ in range(200):
                    if loop.results:
                        break
                    await pilot.pause()
                self.assertTrue(loop.results, "turn never finished")
                self.assertFalse(loop.results[0][0])
                self.assertEqual(len(approver.session_keys), 0)


if __name__ == "__main__":
    unittest.main()
