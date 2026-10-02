"""Processes are seen from outside: kind, line, and the signals they touch."""

import unittest
from pathlib import Path

import rtlscope
from rtlscope import Process
from test_diagram import DiagramAssertions
from verilator_snippets import COMBINATIONAL, buffer_design, loc, varref

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class FixtureProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(COMBINATIONAL)

    def test_each_style_of_mux(self):
        self.assertEqual(
            self.design.modules["mux_procedural"].processes,
            [Process("always_comb", 13, reads=("sel", "a", "b"), writes=("y",))],
        )
        self.assertEqual(
            self.design.modules["mux_continuous"].processes,
            [Process("assign", 25, reads=("sel", "a", "b"), writes=("y",))],
        )

    def test_testbench_initial_block(self):
        # The loop index "i" is local to the block, not a module signal.
        self.assertEqual(self.design.top.processes, [Process("initial", 40, writes=("sel", "a", "b"))])
        self.assertEqual(self.design.top.processes[0].name, "initial, line 40")


class AccessTests(unittest.TestCase):
    def test_read_write_reference_is_both(self):
        process = {
            "type": "ALWAYS",
            "keyword": "always",
            "loc": loc(7),
            "sentreep": [],
            "stmtsp": [dict(varref("(V_x)"), access="RW"), varref("(V_y)")],
        }
        design = rtlscope.parse_verilator_tree(buffer_design(top_stmts=[process]))
        self.assertEqual(design.top.processes, [Process("always", 7, reads=("x", "y"), writes=("x",))])

    def test_other_statements_are_not_processes(self):
        design = rtlscope.parse_verilator_tree(buffer_design(top_stmts=[{"type": "FUNC", "name": "f"}]))
        self.assertEqual(design.top.processes, [])


class FlatModuleTests(DiagramAssertions):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(EXAMPLES / "Vtimer.tree.json")

    def test_processes(self):
        self.assertEqual(
            self.design.top.processes,
            [
                Process("always_comb", 15, reads=("value", "running"), writes=("next",)),
                Process(
                    "always_ff",
                    19,
                    reads=("rst_n", "start", "done", "next"),
                    writes=("value", "running"),
                    clocks=("clk",),
                ),
                Process("assign", 30, reads=("value",), writes=("done",)),
            ],
        )

    def test_diagram(self):
        text = rtlscope.render_diagram(self.design, self.design.top, 120)
        for title in ("always_comb, line 15", "always_ff, line 19", "assign, line 30"):
            self.assertRegex(text, rf"│ +{title} +│")
        self.assertIn("┤▷clk", text)
        self.assertDrawnAsModeled(self.design, self.design.top, text)

    def test_text(self):
        lines = rtlscope.render_text(self.design).splitlines()
        self.assertIn("  process always_ff, line 19", lines)
        self.assertIn("    clock  clk", lines)
        self.assertIn("    reads  rst_n, start, done, next", lines)
        self.assertIn("    writes value, running", lines)


if __name__ == "__main__":
    unittest.main()
