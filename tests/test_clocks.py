"""Clock ports are told apart from asynchronous resets, as synthesis does."""

import unittest
from pathlib import Path

import rtlscope
from diagram_trace import Diagram
from test_diagram import DiagramAssertions
from verilator_snippets import buffer_design, load_combinational, loc, var, varref

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def always_ff(edges, body_reads):
    """An ALWAYS sensitive to (edge, var addr) pairs, whose body reads some vars."""
    return {
        "type": "ALWAYS",
        "keyword": "always_ff",
        "loc": loc(5),
        "sentreep": [
            {
                "type": "SENTREE",
                "sensesp": [
                    {"type": "SENITEM", "edgeType": edge, "sensp": [varref(addr)], "condp": []}
                    for edge, addr in edges
                ],
            }
        ],
        "stmtsp": [{"type": "ASSIGNDLY", "rhsp": [varref(addr) for addr in body_reads], "lhsp": []}],
    }


def clocked_buffer(edges, body_reads):
    """buffer_design whose child has a clk and a rst port and one process."""
    return buffer_design(
        child_stmts=[
            var("(P_clk)", "clk", "INPUT"),
            var("(P_rst)", "rst", "INPUT"),
            always_ff(edges, body_reads),
        ]
    )


def clocks(design, module):
    return [p.name for p in design.modules[module].ports.values() if p.clock]


class ClockRuleTests(unittest.TestCase):
    def test_edge_not_read_by_the_body_is_the_clock(self):
        tree = clocked_buffer([("POS", "(P_clk)"), ("NEG", "(P_rst)")], ["(P_rst)", "(P_in)"])
        self.assertEqual(clocks(rtlscope.parse_verilator_tree(tree), "buffer"), ["clk"])

    def test_negative_edge_clock(self):
        tree = clocked_buffer([("NEG", "(P_clk)")], ["(P_in)"])
        self.assertEqual(clocks(rtlscope.parse_verilator_tree(tree), "buffer"), ["clk"])

    def test_edge_read_by_the_body_is_not_a_clock(self):
        tree = clocked_buffer([("POS", "(P_clk)")], ["(P_clk)"])
        self.assertEqual(clocks(rtlscope.parse_verilator_tree(tree), "buffer"), [])

    def test_level_sensitivity_is_not_a_clock(self):
        tree = clocked_buffer([("CHANGED", "(P_clk)")], [])
        self.assertEqual(clocks(rtlscope.parse_verilator_tree(tree), "buffer"), [])

    def test_combinational_fixture_has_no_clock(self):
        design = rtlscope.parse_verilator_tree(load_combinational())
        for name in design.modules:
            self.assertEqual(clocks(design, name), [], name)


class ClockExampleTests(DiagramAssertions):
    @classmethod
    def setUpClass(cls):
        cls.shift = rtlscope.load_verilator_json(EXAMPLES / "Vshift_register.tree.json")
        cls.counter = rtlscope.load_verilator_json(EXAMPLES / "Vcounter.tree.json")

    def test_async_reset_is_not_a_clock(self):
        self.assertEqual(clocks(self.shift, "dff"), ["clk"])

    def test_clock_propagates_to_the_wrapper(self):
        self.assertEqual(clocks(self.shift, "shift_register"), ["clk"])
        self.assertEqual(clocks(self.counter, "counter"), ["clk"])
        self.assertEqual(clocks(self.counter, "incrementer"), [])

    def test_diagram_marks_clock_pins(self):
        text = rtlscope.render_diagram(self.shift, self.shift.top, 120)
        self.assertEqual(text.count("┤▷clk"), 3)
        self.assertNotIn("▷rst_n", text)
        self.assertEqual({p for b in Diagram(text).boxes for _, p in b["pins"].values()}, {"clk", "rst_n", "d", "q"})
        self.assertDrawnAsModeled(self.shift, self.shift.top, text)

    def test_text_marks_clock_ports(self):
        lines = rtlscope.render_text(self.shift, "dff").splitlines()
        self.assertIn("  input  clk (clock)", lines)
        self.assertIn("  input  rst_n", lines)


if __name__ == "__main__":
    unittest.main()
