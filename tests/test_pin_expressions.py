"""Pin expressions other than a plain VARREF are kept, not guessed at."""

import unittest

import rtlscope
from rtlscope import NetRef, Unsupported
from verilator_snippets import buffer_design, find_pin, load_combinational, loc, var, varref

CONST = {"type": "CONST", "name": "1'h0", "loc": loc(37)}


def sel_of(ref):
    return {"type": "SEL", "name": "", "loc": loc(37), "fromp": [ref], "lsbp": [CONST]}


class UnsupportedExpressionTests(unittest.TestCase):
    def read_with_sel_pin(self, expr):
        tree = load_combinational()
        find_pin(tree, "u_proc", "sel")["exprp"] = [expr]
        return rtlscope.parse_verilator_tree(tree)

    def test_sel_is_explicitly_unsupported(self):
        tree = load_combinational()
        ref = find_pin(tree, "u_proc", "sel")["exprp"][0]
        design = self.read_with_sel_pin(sel_of(ref))
        conns = design.top.instances["u_proc"].connections
        self.assertEqual(conns["sel"], Unsupported("SEL", 37))
        # Only that pin is affected.
        self.assertEqual(conns["y"], NetRef("y_proc"))
        self.assertEqual(design.top.instances["u_cont"].connections["sel"], NetRef("sel"))

    def test_other_expression_kinds(self):
        for kind in ("CONST", "CONCAT", "COND", "AND", "EXTEND"):
            with self.subTest(kind=kind):
                design = self.read_with_sel_pin({"type": kind, "name": "", "loc": loc(37)})
                self.assertEqual(design.top.instances["u_proc"].connections["sel"], Unsupported(kind, 37))

    def test_reference_to_parameter_is_unsupported(self):
        tree = buffer_design(top_stmts=[var("(V_p)", "P", var_type="GPARAM")])
        find_pin(tree, "u_buf", "in")["exprp"] = [varref("(V_p)", line=5)]
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].connections["in"], Unsupported("VARREF to GPARAM", 5))

    def test_unsupported_shows_in_text(self):
        design = self.read_with_sel_pin({"type": "CONCAT", "name": "", "loc": loc(37)})
        lines = rtlscope.render_text(design, "tb_combinational").splitlines()
        self.assertIn("  instance u_proc : mux_procedural", lines)
        self.assertIn("    input  sel <- ? CONCAT (line 37)", lines)


class UnconnectedPinTests(unittest.TestCase):
    def test_empty_expression_is_unconnected(self):
        tree = buffer_design()
        find_pin(tree, "u_buf", "out")["exprp"] = []
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].connections, {"in": NetRef("x")})

    def test_missing_pin_is_unconnected(self):
        tree = buffer_design()
        cell = tree["modulesp"][1]["stmtsp"][2]
        cell["pinsp"] = [p for p in cell["pinsp"] if p["name"] != "out"]
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].connections, {"in": NetRef("x")})
        self.assertIn("    output out (unconnected)", rtlscope.render_text(design, "top").splitlines())


if __name__ == "__main__":
    unittest.main()
