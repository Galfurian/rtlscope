"""Structures the frontend cannot represent are rejected, not dropped."""

import unittest

import rtlscope
from rtlscope import VerilatorJSONError
from verilator_snippets import buffer_design, cell, find_node, find_pin, module, pin, var, varref


def parse(tree):
    return rtlscope.parse_verilator_tree(tree)


class FrontendErrorTests(unittest.TestCase):
    def test_root_must_be_netlist(self):
        for data in ([], {"type": "MODULE"}, {}):
            with self.subTest(data=data):
                with self.assertRaisesRegex(VerilatorJSONError, "root node is not a NETLIST"):
                    parse(data)

    def test_unlinked_modp(self):
        tree = buffer_design()
        find_node(tree, "CELL", "u_buf")["modp"] = "UNLINKED"
        with self.assertRaisesRegex(VerilatorJSONError, r"CELL 'u_buf' \(line 1\): unresolved modp \(UNLINKED\)"):
            parse(tree)

    def test_dangling_mod_varp(self):
        tree = buffer_design()
        find_pin(tree, "u_buf", "in")["modVarp"] = "(NOWHERE)"
        with self.assertRaisesRegex(VerilatorJSONError, r"unresolved modVarp \(\(NOWHERE\)\)"):
            parse(tree)

    def test_mod_varp_must_be_child_port(self):
        tree = buffer_design()
        # Points at a net of the parent instead of a port of the child.
        find_pin(tree, "u_buf", "in")["modVarp"] = "(V_x)"
        with self.assertRaisesRegex(VerilatorJSONError, "modVarp is not a port of buffer"):
            parse(tree)

    def test_unlinked_varp(self):
        tree = buffer_design()
        find_pin(tree, "u_buf", "in")["exprp"][0]["varp"] = "UNLINKED"
        with self.assertRaisesRegex(VerilatorJSONError, "unresolved varp"):
            parse(tree)

    def test_varp_into_another_module(self):
        tree = buffer_design()
        find_pin(tree, "u_buf", "in")["exprp"] = [varref("(P_out)")]
        with self.assertRaisesRegex(VerilatorJSONError, "refers to a variable of module buffer"):
            parse(tree)

    def test_port_connected_twice(self):
        tree = buffer_design()
        u_buf = find_node(tree, "CELL", "u_buf")
        u_buf["pinsp"].append(pin("in_again", "(P_in)", varref("(V_y)")))
        with self.assertRaisesRegex(VerilatorJSONError, "port in of u_buf is connected twice"):
            parse(tree)

    def test_instance_inside_generate_block(self):
        nested = cell("u_gen", "(M1)", line=9)
        tree = buffer_design(top_stmts=[{"type": "GENBLOCK", "name": "g", "stmtsp": [nested]}])
        with self.assertRaisesRegex(VerilatorJSONError, r"CELL 'u_gen' \(line 9\): instances inside GENBLOCK"):
            parse(tree)

    def test_interface_instance(self):
        tree = buffer_design(top_stmts=[cell("u_bus", "(I0)")])
        tree["modulesp"].append(module("(I0)", "bus_if", kind="IFACE"))
        with self.assertRaisesRegex(VerilatorJSONError, "instances of IFACE are not supported"):
            parse(tree)

    def test_instance_array(self):
        tree = buffer_design()
        find_node(tree, "CELL", "u_buf")["rangep"] = [{"type": "RANGE"}]
        with self.assertRaisesRegex(VerilatorJSONError, "instance arrays are not supported"):
            parse(tree)

    def test_ref_port(self):
        tree = buffer_design(child_stmts=[var("(P_r)", "r", "REF")])
        with self.assertRaisesRegex(VerilatorJSONError, "unsupported port direction REF"):
            parse(tree)


if __name__ == "__main__":
    unittest.main()
