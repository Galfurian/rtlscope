"""Each example exercises a construct; check the model got that construct right."""

import unittest
from pathlib import Path

import rtlscope
from rtlscope import Bits, Direction, NetRef, Process, Unsupported

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def load(name):
    return rtlscope.load_verilator_json(EXAMPLES / f"V{name}.tree.json")


def clocks(module):
    return [p.name for p in module.ports.values() if p.clock]


class ConstructTests(unittest.TestCase):
    def test_memories(self):
        regs = load("register_file").top.nets["regs"]
        self.assertEqual((regs.array, regs.bits), ((Bits(0, 3),), Bits(7, 0)))
        self.assertEqual(regs.display, "regs[0:3][7:0]")
        fifo = load("fifo").top
        self.assertEqual(fifo.params, {"DEPTH": "4", "W": "8"})
        self.assertEqual(fifo.nets["mem"].display, "mem[0:3][7:0]")

    def test_connection_styles(self):
        top = load("connection_styles").top
        # .q_n() and a trailing empty positional pin are both unconnected.
        self.assertEqual(top.instances["u_named"].connections, {"clk": NetRef("clk"), "d": NetRef("d"), "q": NetRef("s1")})
        self.assertEqual(top.instances["u_positional"].connections, {"clk": NetRef("clk"), "d": NetRef("s1"), "q": NetRef("s2")})
        # .clk, .q and .q_n connect to the signals of the same name.
        self.assertEqual(
            top.instances["u_dot_name"].connections,
            {"clk": NetRef("clk"), "d": NetRef("s2"), "q": NetRef("q"), "q_n": NetRef("q_n")},
        )

    def test_pin_expressions_are_kept_as_unsupported(self):
        top = load("pin_expressions").top
        self.assertEqual(top.instances["u_slice"].connections["d"].kind, "EXTEND")
        self.assertEqual(top.instances["u_concat"].connections["d"].kind, "CONCAT")
        self.assertEqual(top.instances["u_concat"].connections["en"].kind, "SEL")
        self.assertEqual(top.instances["u_const"].connections["d"], Unsupported("CONST", 21))
        self.assertEqual(top.instances["u_const"].connections["q"], NetRef("q_const"))

    def test_bit_selects_on_every_pin(self):
        top = load("ripple_carry_adder").top
        u0 = top.instances["u0"].connections
        self.assertEqual([u0[p].kind for p in ("a", "b", "s")], ["SEL", "SEL", "SEL"])
        self.assertEqual((u0["cin"], u0["cout"]), (NetRef("cin"), NetRef("c1")))

    def test_two_clock_domains(self):
        self.assertEqual(clocks(load("two_clock_sync").top), ["clk_a", "clk_b"])

    def test_both_edges_of_one_clock(self):
        top = load("negedge_flop").top
        self.assertEqual([p.clocks for p in top.processes], [("clk",), ("clk",)])

    def test_latch_enable_is_not_a_clock(self):
        top = load("latch").top
        self.assertEqual([p.kind for p in top.processes], ["always_latch", "assign"])
        self.assertEqual(clocks(top), [])

    def test_derived_clock(self):
        top = load("clock_divider").top
        # clk_div2 is an output, and it clocks the second divider.
        self.assertEqual(clocks(top), ["clk", "clk_div2"])

    def test_inout(self):
        design = load("tristate")
        self.assertIs(design.top.ports["pin"].direction, Direction.INOUT)
        self.assertIs(design.modules["pad"].ports["io"].direction, Direction.INOUT)
        self.assertEqual(design.top.instances["u_pad"].connections["io"], NetRef("pin"))

    def test_testbench(self):
        design = load("testbench")
        self.assertEqual(design.top.ports, {})
        self.assertEqual(
            design.top.processes,
            [Process("always", 21, reads=("clk",), writes=("clk",)), Process("initial", 23, writes=("rst",))],
        )
        self.assertEqual(clocks(design.modules["dut"]), ["clk"])

    def test_three_levels(self):
        design = load("hierarchy")
        self.assertEqual([m.name for m in design.hierarchy()], ["hierarchy", "core", "acc_alu", "acc_reg"])
        for name in ("hierarchy", "core", "acc_reg"):
            self.assertEqual(clocks(design.modules[name]), ["clk"], name)

    def test_parameter_passed_down(self):
        design = load("parameter_chain")
        self.assertEqual(design.modules["middle__W6"].signature, "middle #(W=6)")
        self.assertEqual(design.modules["leaf__W6"].signature, "leaf #(W=6)")
        self.assertEqual(design.modules["leaf__W6"].ports["d"].bits, Bits(5, 0))

    def test_enum_state(self):
        top = load("traffic_light").top
        self.assertEqual(top.nets["state"].bits, Bits(1, 0))
        self.assertEqual([p.kind for p in top.processes], ["always_ff", "always_comb", "assign", "assign", "assign"])


if __name__ == "__main__":
    unittest.main()
