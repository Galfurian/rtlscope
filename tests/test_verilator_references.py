"""modp, modVarp and varp are followed as pointers, never matched by name."""

import unittest

import rtlscope
from rtlscope import Direction, NetRef
from verilator_snippets import buffer_design, cell, find_node, find_pin, module, netlist, pin, var, varref


class ReferenceResolutionTests(unittest.TestCase):
    def test_buffer_design(self):
        design = rtlscope.parse_verilator_tree(buffer_design())
        self.assertEqual(design.top.name, "top")
        self.assertEqual(design.top.instances["u_buf"].connections, {"in": NetRef("x"), "out": NetRef("y")})

    def test_modp_wins_over_stale_mod_name(self):
        tree = buffer_design()
        find_node(tree, "CELL", "u_buf")["modName"] = "something_else"
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].module, "buffer")

    def test_mod_varp_wins_over_pin_name(self):
        # Positional connections get synthetic pin names.
        tree = buffer_design()
        find_pin(tree, "u_buf", "in")["name"] = "__pinNumber1"
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].connections["in"], NetRef("x"))

    def test_varp_wins_over_varref_name(self):
        tree = buffer_design()
        find_pin(tree, "u_buf", "out")["exprp"][0]["name"] = "not_y"
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.instances["u_buf"].connections["out"], NetRef("y"))

    def test_inout_direction(self):
        tree = netlist(
            module("(M1)", "pad", var("(P_io)", "io", "INOUT")),
            module("(M0)", "top", var("(V_w)", "w"), cell("u_pad", "(M1)", pin("io", "(P_io)", varref("(V_w)")))),
        )
        design = rtlscope.parse_verilator_tree(tree)
        self.assertIs(design.modules["pad"].ports["io"].direction, Direction.INOUT)
        self.assertEqual(design.top.instances["u_pad"].connections, {"io": NetRef("w")})

    def test_parent_port_is_a_valid_net(self):
        tree = buffer_design()
        top = find_node(tree, "MODULE", "top")
        top["stmtsp"][0] = var("(V_x)", "x", "INPUT")
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.ports["x"].direction, Direction.INPUT)
        self.assertNotIn("x", design.top.nets)
        self.assertEqual(design.top.instances["u_buf"].connections["in"], NetRef("x"))

    def test_parameters_are_not_nets(self):
        tree = buffer_design(top_stmts=[var("(V_w)", "WIDTH", var_type="GPARAM")])
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.top.nets, ["x", "y"])

    def test_three_levels_of_hierarchy(self):
        tree = netlist(
            module("(M2)", "leaf", var("(P_l)", "d", "INPUT")),
            module(
                "(M1)",
                "mid",
                var("(P_m)", "d", "INPUT"),
                cell("u_leaf", "(M2)", pin("d", "(P_l)", varref("(P_m)"))),
            ),
            module("(M0)", "top", var("(V_t)", "t"), cell("u_mid", "(M1)", pin("d", "(P_m)", varref("(V_t)")))),
        )
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.tops, ["top"])
        self.assertEqual([m.name for m in design.hierarchy()], ["top", "mid", "leaf"])
        self.assertEqual(design.modules["mid"].instances["u_leaf"].connections, {"d": NetRef("d")})

    def test_multiple_tops_need_a_choice(self):
        tree = netlist(module("(M0)", "a"), module("(M1)", "b"))
        design = rtlscope.parse_verilator_tree(tree)
        self.assertEqual(design.tops, ["a", "b"])
        with self.assertRaisesRegex(rtlscope.RTLScopeError, "exactly one top module, found a, b"):
            design.top


if __name__ == "__main__":
    unittest.main()
