"""Declared ranges reach the model, the text dump and the diagram."""

import unittest
from pathlib import Path

import rtlscope
from rtlscope import Bits, Net, VerilatorJSONError
from diagram_trace import Diagram
from verilator_snippets import LOGIC4, LOGIC8, buffer_design, find_node, load_combinational

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def wide_buffer():
    """buffer_design with every port and net eight bits wide."""
    tree = buffer_design()
    for name in ("in", "out", "x", "y"):
        find_node(tree, "VAR", name)["dtypep"] = LOGIC8
    return tree


class BitsTests(unittest.TestCase):
    def test_width_and_display(self):
        self.assertEqual(Bits(7, 0).width, 8)
        self.assertEqual(Bits(0, 7).width, 8)
        self.assertEqual(str(Bits(0, 7)), "[0:7]")
        self.assertEqual(rtlscope.Port("d", rtlscope.Direction.INPUT, Bits(3, 0)).display, "d[3:0]")
        self.assertEqual(Net("en").display, "en")


class FrontendWidthTests(unittest.TestCase):
    def test_vectors_and_scalars(self):
        design = rtlscope.parse_verilator_tree(wide_buffer())
        self.assertEqual(design.modules["buffer"].ports["in"].bits, Bits(7, 0))
        self.assertEqual(design.top.nets["x"], Net("x", Bits(7, 0)))
        scalar = rtlscope.parse_verilator_tree(buffer_design())
        self.assertIsNone(scalar.top.nets["x"].bits)

    def test_typedef_and_enum_follow_their_base_type(self):
        tree = buffer_design()
        tree["miscsp"][0]["typesp"] += [
            {"type": "REFDTYPE", "name": "byte_t", "addr": "(R1)", "refDTypep": LOGIC8},
            {"type": "REFDTYPE", "name": "state_t", "addr": "(R2)", "refDTypep": "(E1)"},
            {"type": "ENUMDTYPE", "name": "state_t", "addr": "(E1)", "refDTypep": LOGIC4},
        ]
        find_node(tree, "VAR", "x")["dtypep"] = "(R1)"
        find_node(tree, "VAR", "y")["dtypep"] = "(R2)"
        top = rtlscope.parse_verilator_tree(tree).top
        self.assertEqual(top.nets["x"].bits, Bits(7, 0))
        self.assertEqual(top.nets["y"].bits, Bits(3, 0))

    def test_unsupported_types_are_errors(self):
        for node, message in (
            ({"type": "PACKARRAYDTYPE", "addr": "(A1)", "refDTypep": LOGIC4}, "multi-dimensional packed arrays not supported"),
            ({"type": "STRUCTDTYPE", "addr": "(A1)"}, "data type STRUCTDTYPE not supported"),
        ):
            with self.subTest(type=node["type"]):
                tree = buffer_design()
                tree["miscsp"][0]["typesp"].append(node)
                find_node(tree, "VAR", "x")["dtypep"] = "(A1)"
                with self.assertRaisesRegex(VerilatorJSONError, rf"VAR 'x' \(line 1\): {message}"):
                    rtlscope.parse_verilator_tree(tree)

    def test_unresolved_dtype(self):
        tree = buffer_design()
        find_node(tree, "VAR", "x")["dtypep"] = "UNLINKED"
        with self.assertRaisesRegex(VerilatorJSONError, "unresolved dtypep"):
            rtlscope.parse_verilator_tree(tree)

    def test_real_fixture_is_all_single_bit(self):
        design = rtlscope.parse_verilator_tree(load_combinational())
        for module in design.modules.values():
            for signal in [*module.ports.values(), *module.nets.values()]:
                self.assertIsNone(signal.bits, f"{module.name}.{signal.name}")


class ExampleWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.counter = rtlscope.load_verilator_json(EXAMPLES / "Vcounter.tree.json")

    def test_counter_model(self):
        top = self.counter.top
        self.assertIsNone(top.ports["clk"].bits)
        self.assertEqual(top.ports["count"].bits, Bits(3, 0))
        self.assertEqual(top.nets["next"].bits, Bits(3, 0))
        self.assertEqual(self.counter.modules["register"].ports["d"].bits, Bits(3, 0))

    def test_counter_text(self):
        lines = rtlscope.render_text(self.counter, "counter").splitlines()
        self.assertIn("  output count[3:0]", lines)
        self.assertIn("  net    next[3:0]", lines)
        self.assertIn("    input  d[3:0] <- next", lines)

    def test_counter_diagram(self):
        text = rtlscope.render_diagram(self.counter, self.counter.top, 120)
        self.assertIn("▶ count[3:0]", text)
        box = Diagram(text).box("u_inc")
        self.assertIn(("u_inc", "a"), box["pins"].values())
        self.assertRegex(text, r"┤a\[3:0\] +y\[3:0\]├")


if __name__ == "__main__":
    unittest.main()


class ArrayTests(unittest.TestCase):
    def test_unpacked_dimensions_outermost_first(self):
        tree = buffer_design()
        tree["miscsp"][0]["typesp"] += [
            {"type": "UNPACKARRAYDTYPE", "addr": "(U1)", "declRange": "[0:3]", "refDTypep": "(U2)"},
            {"type": "UNPACKARRAYDTYPE", "addr": "(U2)", "declRange": "[1:0]", "refDTypep": LOGIC8},
        ]
        find_node(tree, "VAR", "x")["dtypep"] = "(U1)"
        net = rtlscope.parse_verilator_tree(tree).top.nets["x"]
        self.assertEqual(net.array, (Bits(0, 3), Bits(1, 0)))
        self.assertEqual(net.bits, Bits(7, 0))
        self.assertEqual(net.display, "x[0:3][1:0][7:0]")
