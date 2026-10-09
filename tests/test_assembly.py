"""Red-first tests for T3: blank-slate assembly.

Spec: docs/assembly-spec.md section 3.
Order: sats (facts, decisions, tasks) -> history -> scratch.
"""
import tempfile
import unittest
from pathlib import Path

from harness.assembly import assemble, load_prompt, write_assembled
from harness.context import parse_transcript
from harness.session import init_layout


def make_session():
    td = tempfile.mkdtemp()
    sdir = Path(td) / "sess"
    init_layout(sdir, "SYS {ctx_path} {hard} {soft}")
    return sdir


class TestAssembly(unittest.TestCase):
    def test_order_sats_history_scratch(self):
        sdir = make_session()
        (sdir / "sats" / "facts.md").write_text(
            "# Facts\n\n## anchor-a\nfact one\n", encoding="utf-8")
        (sdir / "sats" / "decisions.md").write_text(
            "# Decisions\n\n## anchor-b\ndecision one\n", encoding="utf-8")
        (sdir / "history.md").write_text(
            "## user t0001\nhi\n", encoding="utf-8")
        (sdir / "scratch.md").write_text(
            "## tool c1\nresult\n", encoding="utf-8")
        text = assemble(sdir)
        i_facts = text.index("fact one")
        i_dec = text.index("decision one")
        i_hist = text.index("## user t0001")
        i_scratch = text.index("## tool c1")
        self.assertLess(i_facts, i_dec)
        self.assertLess(i_dec, i_hist)
        self.assertLess(i_hist, i_scratch)

    def test_deterministic_reassembly(self):
        sdir = make_session()
        (sdir / "history.md").write_text("## user t0001\nhi\n",
                                         encoding="utf-8")
        self.assertEqual(assemble(sdir), assemble(sdir))

    def test_fresh_layout_assembles(self):
        sdir = make_session()
        text = assemble(sdir)
        # Satellites are present even when empty (model learns they exist).
        self.assertIn("## sat facts", text)
        self.assertIn("## sat decisions", text)
        self.assertIn("## sat tasks", text)

    def test_sat_sections_parse_as_user_messages(self):
        sdir = make_session()
        (sdir / "sats" / "facts.md").write_text(
            "# Facts\n\n## anchor-a\nfact one\n", encoding="utf-8")
        msgs = parse_transcript(assemble(sdir))
        sat_msgs = [m for m in msgs if m["role"] == "user"
                    and "fact one" in (m["content"] or "")]
        self.assertEqual(len(sat_msgs), 1)

    def test_history_and_scratch_sections_parse(self):
        sdir = make_session()
        (sdir / "history.md").write_text("## user t0001\nhi\n",
                                         encoding="utf-8")
        (sdir / "scratch.md").write_text("## tool c1\nresult\n",
                                         encoding="utf-8")
        msgs = parse_transcript(assemble(sdir))
        roles = [m["role"] for m in msgs]
        # sats (3 user msgs) + history user; orphan tool dropped.
        self.assertIn("user", roles)

    def test_load_prompt_formats_placeholders(self):
        sdir = make_session()
        out = load_prompt(sdir, ctx_path="CTX", hard=1000, soft=800)
        self.assertEqual(out, "SYS CTX 1000 800")

    def test_load_prompt_fills_os_and_workdir(self):
        import platform
        sdir = make_session()
        (sdir / "prompt.md").write_text(
            "os={os_name} workdir={workdir}", encoding="utf-8")
        out = load_prompt(sdir, ctx_path="CTX", hard=1, soft=1,
                          workdir="/tmp/w")
        self.assertIn(f"os={platform.system()}", out)
        self.assertIn("workdir=/tmp/w", out)
        self.assertNotIn("{os_name}", out)
        self.assertNotIn("{workdir}", out)

    def test_load_prompt_leaves_other_braces_alone(self):
        sdir = make_session()
        (sdir / "prompt.md").write_text(
            "do {ctx_path} and keep {other} literal", encoding="utf-8")
        out = load_prompt(sdir, ctx_path="CTX", hard=1, soft=1)
        self.assertIn("{other}", out)
        self.assertIn("CTX", out)

    def test_write_assembled_writes_context_md(self):
        sdir = make_session()
        p = write_assembled(sdir, "hello")
        self.assertEqual(p, sdir / "context.md")
        self.assertEqual(p.read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main()
