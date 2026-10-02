"""Parameterized modules show their source name and values, not Verilator's."""

import unittest
from pathlib import Path

import rtlscope
from diagram_trace import Diagram
from test_diagram import DiagramAssertions
from verilator_snippets import buffer_design, loc

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def param(addr, name, value, var_type="GPARAM"):
    return {
        "type": "VAR", "name": name, "addr": addr, "loc": loc(1), "dtypep": "(T1)",
        "direction": "NONE", "varType": var_type,
        "valuep": [{"type": "CONST", "name": value, "loc": loc(1)}],
    }


class ConstantTests(unittest.TestCase):
    def test_verilator_constants(self):
        for text, expected in (
            ("32'sh4", "4"),
            ("32'h10", "16"),
            ("4'b1010", "10"),
            ("8'sb11111111", "-1"),
            ("32'sd7", "7"),
            ('"name"', '"name"'),
            ("32'hx", "32'hx"),
        ):
            with self.subTest(text=text):
                self.assertEqual(rtlscope._const_text(text), expected)


class FrontendParameterTests(unittest.TestCase):
    def test_overridable_parameters_only(self):
        tree = buffer_design(child_stmts=[param("(G1)", "W", "32'sh4"), param("(L1)", "HALF", "32'sh2", "LPARAM")])
        tree["modulesp"][0]["origName"] = "buffer"
        tree["modulesp"][0]["name"] = "buffer__W4"
        design = rtlscope.parse_verilator_tree(tree)
        child = design.modules["buffer__W4"]
        self.assertEqual(child.source, "buffer")
        self.assertEqual(child.params, {"W": "4"})
        self.assertEqual(child.signature, "buffer #(W=4)")
        self.assertEqual(design.top.instances["u_buf"].module, "buffer__W4")

    def test_plain_module(self):
        design = rtlscope.parse_verilator_tree(buffer_design())
        self.assertEqual(design.top.signature, "top")
        self.assertEqual(rtlscope.render_text(design, "top").splitlines()[0], "module top")


class ParameterExampleTests(DiagramAssertions):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(EXAMPLES / "Vparameters.tree.json")

    def test_both_specializations(self):
        narrow = self.design.modules["bit_reverse__W4"]
        default = self.design.modules["bit_reverse"]
        self.assertEqual((narrow.source, narrow.params), ("bit_reverse", {"WIDTH": "4"}))
        self.assertEqual((default.source, default.params), ("bit_reverse", {"WIDTH": "8"}))

    def test_text(self):
        lines = rtlscope.render_text(self.design).splitlines()
        self.assertIn("  instance u4 : bit_reverse #(WIDTH=4)", lines)
        self.assertIn("  instance u8 : bit_reverse #(WIDTH=8)", lines)
        self.assertIn("module bit_reverse__W4  (bit_reverse #(WIDTH=4))", lines)

    def test_diagram_hides_the_mangled_name(self):
        text = rtlscope.render_diagram(self.design, self.design.top, 100)
        self.assertNotIn("__W4", text)
        drawing = Diagram(text)
        for name, width in (("u4", "4"), ("u8", "8")):
            box = drawing.box(name)
            rows = drawing.rows[box["y"] + 1 : box["y"] + 3]
            self.assertEqual(rows[0].strip().strip("│").strip(), f"{name} : bit_reverse")
            self.assertIn(f"#(WIDTH={width})", rows[1])
        self.assertDrawnAsModeled(self.design, self.design.top, text)


if __name__ == "__main__":
    unittest.main()
