"""Phase 3 TUI tests: widgets pure fns, CLI lines parity, slash dispatch.

Stdlib-only except the Textual app/widget checks, which skip cleanly
when the extra is missing.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from harness import __main__ as cli
from harness.tui import has_tui
from harness.tui import commands
from harness.tui import widgets
from harness.tui.bridge import (TranscriptDedupe, budget_bar_status,
                                budget_bar_text, dedupe_entries,
                                format_event, format_status)


def fake_tracker():
    t = SimpleNamespace(turns=[], totals={"input": 7, "output": 3,
                                          "estimated": 100})
    for i in (1, 2, 3):
        t.turns.append({"ts": "t", "turn": i, "phase": "main", "step": 0,
                        "breakdown": {"system": 10, "transcript": 20,
                                      "tools": 5, "total": 35},
                        "server": {"input": 1, "output": 2}})
    return t


def fake_cfg(**kw):
    base = {"provider": "openai", "model": "gpt-5",
            "base_url": "https://api.openai.com/v1",
            "budget_hard": 100000, "budget_soft": 80000,
            "approval_timeout": 120, "exec_timeout": 60,
            "exec_timeout_max": 300, "request_timeout": 120,
            "usage_note": True, "prune_target": None,
            "prune_keep_tools": 5, "prune_section_cap": 8000,
            "sessions_dir": "/tmp/sess", "api_key": None}
    base.update(kw)
    return SimpleNamespace(**base)


def printed(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().splitlines()


class TestWidgetsPure(unittest.TestCase):
    def test_bridge_reexport_identical(self):
        # The move must not fork logic: same function objects.
        self.assertIs(widgets.format_event, format_event)
        self.assertIs(widgets.budget_bar_text, budget_bar_text)
        self.assertIs(widgets.budget_bar_status, budget_bar_status)
        self.assertIs(widgets.format_status, format_status)
        self.assertIs(widgets.dedupe_entries, dedupe_entries)
        self.assertIs(widgets.TranscriptDedupe, TranscriptDedupe)

    def test_tail_file_last_n(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "debug.jsonl"
            p.write_text("\n".join(f"line {i}" for i in range(60)),
                         encoding="utf-8")
            got = widgets.tail_file(p, 50)
            self.assertEqual(len(got), 50)
            self.assertEqual(got[0], "line 10")
            self.assertEqual(got[-1], "line 59")

    def test_tail_file_missing(self):
        self.assertEqual(widgets.tail_file(None), [])
        self.assertEqual(widgets.tail_file("/no/such/file.jsonl"), [])

    def test_debug_panel_hint(self):
        self.assertIn("--debug",
                      widgets.debug_panel_lines(None)[0])
        self.assertIn("--debug",
                      widgets.debug_panel_lines("/no/such/f")[0])

    def test_debug_panel_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "debug.jsonl"
            p.write_text('{"kind": "request"}\n', encoding="utf-8")
            self.assertEqual(widgets.debug_panel_lines(p),
                             ['{"kind": "request"}'])


class TestCliLinesParity(unittest.TestCase):
    """TUI views reuse the CLI's own lines helpers (no fork)."""

    def test_config(self):
        cfg = fake_cfg()
        self.assertEqual(printed(cli._show_config, cfg),
                         cli.config_lines(cfg))
        self.assertEqual(cli.config_lines(cfg)[0], "[config]")
        blob = "\n".join(cli.config_lines(cfg))
        self.assertNotIn("api_key", blob.replace("api_key_env", ""))

    def test_providers(self):
        self.assertEqual(printed(cli._show_providers),
                         cli.providers_lines())
        self.assertEqual(cli.providers_lines()[0], "[providers]")

    def test_usage(self):
        t = fake_tracker()
        for rest in ("", "2", "bogus", "0"):
            self.assertEqual(printed(cli._show_usage, t, rest),
                             cli.usage_lines(t, rest))
        self.assertIn("over 3 calls", cli.usage_lines(t, "")[0])
        self.assertEqual(len(cli.usage_lines(t, "2")), 3)
        self.assertEqual(cli.usage_lines(None, ""), ["No usage recorded yet."])
        self.assertEqual(cli.usage_lines(SimpleNamespace(turns=[]), ""),
                         ["No usage recorded yet."])

    def test_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            for sid in ("20261005-080000", "20261005-090000"):
                (Path(d) / sid).mkdir()
            (Path(d) / ".current").write_text("20261005-090000")
            cfg = SimpleNamespace(sessions_path=Path(d))
            self.assertEqual(printed(cli._list_sessions, cfg),
                             cli.sessions_lines(cfg))
            self.assertIn("20261005-090000 *", cli.sessions_lines(cfg))

    def test_session_header(self):
        from harness.loop import Session
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="s1", dir=Path(d), workdir=d)
            cfg = fake_cfg()
            line = cli.session_header_line(cfg, s, None, None)
            self.assertEqual(printed(cli._show_session, cfg, s, None, None),
                             [line])
            self.assertIn("s1", line)

    def test_project_status(self):
        self.assertEqual(cli.project_status_lines("p", ["a", "b"]),
                         ["project: p  (known: a, b)"])
        self.assertEqual(cli.project_status_lines(None, []),
                         ["project: (none)"])

    def test_models_unsupported_provider(self):
        cfg = fake_cfg(provider="anthropic")
        self.assertEqual(cli.models_lines(cfg),
                         ["[models] provider anthropic does not support "
                          "/v1/models listing"])


class TestSlashDispatch(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(commands.parse_slash("hello world"))
        self.assertIsNone(commands.parse_slash("/"))
        self.assertEqual(commands.parse_slash("/list"), ("list", ""))
        self.assertEqual(commands.parse_slash("/HELP"), ("help", ""))
        self.assertEqual(commands.parse_slash("/new do the thing"),
                         ("new", "do the thing"))
        self.assertEqual(commands.parse_slash("/open 20261005"),
                         ("open", "20261005"))

    def test_unknown_matches_cli(self):
        hint = commands.unknown_hint("bogus")
        self.assertEqual(hint, "Unknown command /bogus (/help).")
        self.assertEqual(commands.local_lines("bogus", ""), [hint])

    def test_help_is_repl_help(self):
        self.assertEqual(commands.local_lines("help", ""),
                         cli.REPL_HELP.splitlines())

    def test_readonly_reuse_cli_helpers(self):
        t, cfg = fake_tracker(), fake_cfg()
        self.assertEqual(commands.local_lines("usage", "2", tracker=t),
                         cli.usage_lines(t, "2"))
        self.assertEqual(commands.local_lines("config", "", cfg=cfg),
                         cli.config_lines(cfg))
        self.assertEqual(commands.local_lines("providers", ""),
                         cli.providers_lines())

    def test_list_via_callback(self):
        self.assertEqual(commands.local_lines("list", "",
                                              list_lines=lambda: ["a *"]),
                         ["a *"])

    def test_state_and_worker_need_caller(self):
        for cmd in ("new", "open", "session", "project",
                    "models", "quit", "exit", "q"):
            self.assertIsNone(commands.local_lines(cmd, "x"))
        self.assertTrue(commands.is_quit("quit"))
        self.assertTrue(commands.is_quit("q"))
        self.assertFalse(commands.is_quit("list"))
        self.assertTrue(commands.needs_worker("models"))
        self.assertFalse(commands.needs_worker("usage"))


class TestAppSource(unittest.TestCase):
    def test_no_debug_path_shadowing(self):
        """Regression: self._debug_path must stay a method (issue: ctrl+d
        crashed with 'NoneType is not callable' because __init__ stored
        the static path under the same name)."""
        import ast
        src = Path(__file__).parent.parent.joinpath(
            "harness", "tui", "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        assigned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(
                    node.value, ast.Name) and node.value.id == "self" \
                    and isinstance(node.ctx, ast.Store):
                assigned.add(node.attr)
        self.assertNotIn("_debug_path", assigned)
        self.assertIn("_debug_path_static", assigned)


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestAppPhase3(unittest.TestCase):
    def test_bindings_and_handlers(self):
        from harness.tui.app import HarnessApp
        keys = {b[0] for b in HarnessApp.BINDINGS}
        self.assertIn("ctrl+d", keys)
        for name in ("_handle_slash", "_fetch_models", "_sync_state",
                     "action_toggle_debug", "_refresh_debug",
                     "_debug_path"):
            self.assertTrue(hasattr(HarnessApp, name), name)

    def test_thin_widgets_exist(self):
        from harness.tui.widgets import (BudgetBar, DebugPanel,
                                         StatusLine)
        self.assertTrue(BudgetBar and DebugPanel and StatusLine)


if __name__ == "__main__":
    unittest.main()
