"""The real Verilator tree of tb_combinational, read into the model."""

import unittest

import rtlscope
from rtlscope import Direction, NetRef, Port
from verilator_snippets import COMBINATIONAL

MUX_PORTS = {
    "sel": Port("sel", Direction.INPUT),
    "a": Port("a", Direction.INPUT),
    "b": Port("b", Direction.INPUT),
    "y": Port("y", Direction.OUTPUT),
}


class CombinationalFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(COMBINATIONAL)

    def test_finds_every_module(self):
        self.assertEqual(
            list(self.design.modules),
            ["tb_combinational", "mux_procedural", "mux_continuous"],
        )

    def test_constant_pool_is_not_a_module(self):
        # @CONST-POOL@ is a MODULE node too, but it lives under miscsp.
        self.assertNotIn("@CONST-POOL@", self.design.modules)

    def test_top_module(self):
        self.assertEqual(self.design.tops, ["tb_combinational"])
        self.assertEqual(self.design.top.name, "tb_combinational")

    def test_extracts_cells_in_source_order(self):
        self.assertEqual(list(self.design.top.instances), ["u_proc", "u_cont"])

    def test_cell_modp_resolves_to_child_module(self):
        top = self.design.top
        self.assertEqual(top.instances["u_proc"].module, "mux_procedural")
        self.assertEqual(top.instances["u_cont"].module, "mux_continuous")

    def test_child_ports_and_directions(self):
        for name in ("mux_procedural", "mux_continuous"):
            with self.subTest(module=name):
                module = self.design.modules[name]
                self.assertEqual(module.ports, MUX_PORTS)
                self.assertEqual(list(module.ports), ["sel", "a", "b", "y"])

    def test_child_modules_are_leaves(self):
        # always_comb, assign and their expressions are not structure.
        for name in ("mux_procedural", "mux_continuous"):
            with self.subTest(module=name):
                self.assertEqual(self.design.modules[name].instances, {})
                self.assertEqual(self.design.modules[name].nets, {})

    def test_top_nets_exclude_block_locals(self):
        # The loop variable "i" is declared inside the initial block.
        top = self.design.top
        self.assertEqual(top.ports, {})
        self.assertEqual(list(top.nets), ["sel", "a", "b", "y_proc", "y_cont"])

    def test_expected_connectivity(self):
        top = self.design.top
        self.assertEqual(
            top.instances["u_proc"].connections,
            {"sel": NetRef("sel"), "a": NetRef("a"), "b": NetRef("b"), "y": NetRef("y_proc")},
        )
        self.assertEqual(
            top.instances["u_cont"].connections,
            {"sel": NetRef("sel"), "a": NetRef("a"), "b": NetRef("b"), "y": NetRef("y_cont")},
        )

    def test_hierarchy_order(self):
        self.assertEqual(
            [m.name for m in self.design.hierarchy()],
            ["tb_combinational", "mux_procedural", "mux_continuous"],
        )


if __name__ == "__main__":
    unittest.main()
