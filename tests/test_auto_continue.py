"""Goal-driven auto-continue (docs/goal-auto-continue-spec.md).

When a turn ends with any `- [active]` goal and auto_continue is
enabled, the harness starts another full turn instead of waiting for
input, up to auto_continue_max_turns per user message.
"""
import tempfile
import unittest
from pathlib import Path

from harness.config import Config
from harness.context import Budget
from harness.loop import AUTO_CONTINUE_TEMPLATE, Loop, Session
from harness.providers import MockProvider
from harness.session import active_goals, add_goal


def make_session():
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=d)


def handoff_turn(sdir, turn_no, extra_calls=None):
    """One handoff-compliant mock turn: rewrite the stamp, then reply.

    turn_no is the 1-based turn counter value this turn will carry.
    extra_calls are additional tool calls executed after the handoff
    edit (e.g. flipping a goal to [done]).
    """
    calls = [{
        "id": f"handoff-t{turn_no:04d}", "name": "edit",
        "arguments": {
            "path": str(Path(sdir) / "sats" / "handoff.md"),
            "old_text": f"## Handoff -- t{turn_no - 1:04d}",
            "new_text": f"## Handoff -- t{turn_no:04d}"}}]
    calls.extend(extra_calls or [])
    return [
        {"content": None, "tool_calls": calls},
        {"content": "done"},
    ]


def make_loop(provider, **kw):
    events = []
    loop = Loop(provider, Budget(hard=100000, soft=80000),
                on_event=lambda k, v: events.append((k, v)), **kw)
    return loop, events


def history(s):
    return (s.dir / "history.md").read_text(encoding="utf-8")


class TestAutoContinueDefaults(unittest.TestCase):
    def test_config_defaults_off(self):
        cfg = Config()
        self.assertFalse(cfg.auto_continue)
        self.assertEqual(cfg.auto_continue_max_turns, 3)

    def test_loop_defaults_off(self):
        loop, _ = make_loop(MockProvider([]))
        self.assertFalse(loop.auto_continue)
        self.assertEqual(loop._auto_continue_max_turns, 3)

    def test_bad_max_turns_coerced_safe(self):
        loop, _ = make_loop(MockProvider([]), auto_continue=True,
                            auto_continue_max_turns="bogus")
        self.assertEqual(loop._auto_continue_max_turns, 0)


class TestAutoContinueChain(unittest.TestCase):
    def test_disabled_by_default_no_chain(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        loop, _ = make_loop(MockProvider(handoff_turn(s.dir, 1)))
        loop.run_turn(s, "go")
        body = history(s)
        self.assertIn("## user t0001", body)
        self.assertNotIn("## auto", body)

    def test_chains_while_goal_active(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        script = (handoff_turn(s.dir, 1) + handoff_turn(s.dir, 2)
                  + handoff_turn(s.dir, 3))
        loop, events = make_loop(MockProvider(script), auto_continue=True,
                                 auto_continue_max_turns=2)
        loop.run_turn(s, "go")
        body = history(s)
        self.assertIn("## user t0001", body)
        self.assertIn("## auto t0002", body)
        self.assertIn("## auto t0003", body)
        # Synthesized message recorded verbatim, with chain position.
        self.assertIn("[auto-continue 1/2]", body)
        self.assertIn("[auto-continue 2/2]", body)
        self.assertIn("trace the thing", body)
        # Event emitted per auto-turn.
        auto_events = [v for k, v in events if k == "auto-continue"]
        self.assertEqual([v["index"] for v in auto_events], [1, 2])
        # No handoff rounds exist (the harness renders): exactly 2
        # provider calls per turn.
        self.assertEqual(len(loop.provider.calls), 6)

    def test_cap_respected(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        script = handoff_turn(s.dir, 1) + handoff_turn(s.dir, 2)
        loop, _ = make_loop(MockProvider(script), auto_continue=True,
                            auto_continue_max_turns=1)
        loop.run_turn(s, "go")
        body = history(s)
        self.assertIn("## auto t0002", body)
        self.assertNotIn("## auto t0003", body)

    def test_no_chain_without_active_goal(self):
        s = make_session()
        loop, _ = make_loop(MockProvider(handoff_turn(s.dir, 1)),
                            auto_continue=True, auto_continue_max_turns=3)
        loop.run_turn(s, "go")
        self.assertNotIn("## auto", history(s))

    def test_stops_when_goal_marked_done(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        done_flip = {
            "id": "goal-done", "name": "edit",
            "arguments": {
                "path": str(s.dir / "sats" / "goals.md"),
                "old_text": "- [active] trace the thing",
                "new_text": "- [done] trace the thing"}}
        script = (handoff_turn(s.dir, 1)
                  + handoff_turn(s.dir, 2, extra_calls=[done_flip]))
        loop, _ = make_loop(MockProvider(script), auto_continue=True,
                            auto_continue_max_turns=3)
        loop.run_turn(s, "go")
        body = history(s)
        self.assertIn("## auto t0002", body)
        self.assertNotIn("## auto t0003", body)
        self.assertEqual(active_goals(s.dir), [])

    def test_interrupt_stops_chain(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        holder = {}

        class InterruptProvider(MockProvider):
            def chat(self, **kw):
                resp = super().chat(**kw)
                # Arm the interrupt while the turn-1 "done" is in flight:
                # the post-chat _check_stop must kill the turn and the chain.
                if len(self.calls) == 2:
                    holder["loop"]._stop_event.set()
                return resp

        provider = InterruptProvider(handoff_turn(s.dir, 1))
        loop, _ = make_loop(provider, auto_continue=True,
                            auto_continue_max_turns=3)
        holder["loop"] = loop
        out = loop.run_turn(s, "go")
        self.assertEqual(out, "")
        self.assertNotIn("## auto", history(s))

    def test_stop_between_turns_honored(self):
        s = make_session()
        add_goal(s.dir, "trace the thing")
        loop, _ = make_loop(MockProvider(handoff_turn(s.dir, 1)),
                            auto_continue=True, auto_continue_max_turns=3)
        # Stop arrives after the turn completed normally (event consumed
        # by the turn): the chain must not start another turn.
        orig = loop._run_turn_once

        def once(*a, **kw):
            out = orig(*a, **kw)
            loop._stop_event.set()
            return out

        loop._run_turn_once = once
        loop.run_turn(s, "go")
        body = history(s)
        self.assertIn("## user t0001", body)
        self.assertNotIn("## auto", body)


class TestAutoContinueTemplate(unittest.TestCase):
    def test_template_format(self):
        msg = AUTO_CONTINUE_TEMPLATE % (1, 3, "do X")
        self.assertIn("[auto-continue 1/3]", msg)
        self.assertIn("do X", msg)

    def test_goal_with_braces_does_not_crash(self):
        # Goal text is model/user-controlled: braces must survive.
        msg = AUTO_CONTINUE_TEMPLATE % (1, 3, "fix {bug} in {file}")
        self.assertIn("fix {bug} in {file}", msg)


if __name__ == "__main__":
    unittest.main()
