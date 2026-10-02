"""The block diagram draws exactly the connectivity of the model."""

import random
import unittest

import rtlscope
from rtlscope import Design, DiagramError, Direction, Instance, Module, Net, NetRef, Port, Unsupported
from diagram_trace import Diagram
from verilator_snippets import COMBINATIONAL

IN, OUT, IO = Direction.INPUT, Direction.OUTPUT, Direction.INOUT

COMBINATIONAL_DIAGRAM = """\
            ┌─────────────────────────┐
            │ u_proc : mux_procedural │
sel ─────┬──┤sel                     y├─────── y_proc
a ──────┬┼──┤a                        │
b ─────┬┼┼──┤b                        │
       │││  └─────────────────────────┘
       │││
       │││  ┌─────────────────────────┐
       │││  │ u_cont : mux_continuous │
       ││└──┤sel                     y├─────── y_cont
       │└───┤a                        │
       └────┤b                        │
            └─────────────────────────┘
"""


def cell_type(name, *ports):
    return Module(name, ports={p: Port(p, d) for p, d in ports})


def design(top_ports, nets, cell_types, instances):
    """instances: (name, cell type, {port: net})."""
    top = Module(
        "top",
        ports={p: Port(p, d) for p, d in top_ports},
        nets={n: Net(n) for n in nets},
        instances={
            name: Instance(name, kind, {port: NetRef(net) for port, net in conns.items()})
            for name, kind, conns in instances
        },
    )
    return Design({"top": top, **{c.name: c for c in cell_types}})


BUF = cell_type("buf", ("i", IN), ("o", OUT))
AND2 = cell_type("and2", ("a", IN), ("b", IN), ("y", OUT))


class DiagramAssertions(unittest.TestCase):
    def assertDrawnAsModeled(self, design, module, text):
        drawn = Diagram(text).nets()
        for ends in drawn:
            self.assertFalse([e for e in ends if e[0] == "dangling"], f"dangling wire in\n{text}")
        expected = {}
        for net in [*module.ports, *module.nets]:
            pins = frozenset(
                ("pin", inst.name, port)
                for inst in module.instances.values()
                for port, expr in inst.connections.items()
                if expr == NetRef(net)
            )
            if pins:
                expected[pins] = net
        by_pins = {frozenset(e for e in ends if e[0] == "pin"): ends for ends in drawn}
        self.assertEqual(set(by_pins), set(expected), f"connectivity differs in\n{text}")
        for pins, net in expected.items():
            labels = {e[1] for e in by_pins[pins] if e[0] == "label"}
            self.assertLessEqual(labels, {net}, f"net {net} carries a foreign label in\n{text}")


class CombinationalDiagramTests(DiagramAssertions):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(COMBINATIONAL)
        cls.text = rtlscope.render_diagram(cls.design, cls.design.top, 80)

    def test_snapshot(self):
        self.assertEqual(self.text, COMBINATIONAL_DIAGRAM)

    def test_connectivity(self):
        self.assertDrawnAsModeled(self.design, self.design.top, self.text)

    def test_undriven_nets_are_labels(self):
        nets = {frozenset(ends) for ends in Diagram(self.text).nets()}
        self.assertIn(
            frozenset({("pin", "u_proc", "sel"), ("pin", "u_cont", "sel"), ("label", "sel")}),
            nets,
        )
        self.assertIn(frozenset({("pin", "u_proc", "y"), ("label", "y_proc")}), nets)

    def test_fits_its_width(self):
        self.assertLessEqual(max(map(len, self.text.splitlines())), 80)


class PlacementTests(DiagramAssertions):
    def test_chain_flows_left_to_right(self):
        d = design(
            [("din", IN), ("dout", OUT)],
            ["n1", "n2"],
            [BUF],
            [
                ("u3", "buf", {"i": "n2", "o": "dout"}),
                ("u1", "buf", {"i": "din", "o": "n1"}),
                ("u2", "buf", {"i": "n1", "o": "n2"}),
            ],
        )
        text = rtlscope.render_diagram(d, d.top, 100)
        drawing = Diagram(text)
        xs = [drawing.box(name)["x"] for name in ("u1", "u2", "u3")]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(xs)), 3)
        self.assertIn("din ▶", text)
        self.assertIn("▶ dout", text)
        self.assertDrawnAsModeled(d, d.top, text)

    def test_fanout_and_skip_connection(self):
        d = design(
            [("x", IN), ("y", OUT)],
            ["n1", "n2"],
            [BUF, AND2],
            [
                ("u1", "buf", {"i": "x", "o": "n1"}),
                ("u2", "buf", {"i": "n1", "o": "n2"}),
                ("u3", "and2", {"a": "n1", "b": "n2", "y": "y"}),
            ],
        )
        text = rtlscope.render_diagram(d, d.top, 100)
        self.assertDrawnAsModeled(d, d.top, text)

    def test_output_label_avoids_a_blocked_row(self):
        # u1.o drives the output and u2, and u2 sits on u1.o's row. The label
        # must not stay on that row and wrap around u2 to reach the edge.
        d = design(
            [("x", IN), ("out", OUT)],
            ["n"],
            [BUF],
            [("u1", "buf", {"i": "x", "o": "out"}), ("u2", "buf", {"i": "out", "o": "n"})],
        )
        text = rtlscope.render_diagram(d, d.top, 80)
        drawing = Diagram(text)
        u2 = drawing.box("u2")
        u2_rows = {y for _, y in u2["cells"]}
        label_row = next(y for y, row in enumerate(text.splitlines()) if "▶ out" in row)
        self.assertNotIn(label_row, u2_rows, text)
        self.assertDrawnAsModeled(d, d.top, text)

    def test_feedback_loop(self):
        d = design(
            [("a", IN), ("q", OUT)],
            ["fb"],
            [AND2, BUF],
            [
                ("u_and", "and2", {"a": "a", "b": "fb", "y": "q"}),
                ("u_inv", "buf", {"i": "q", "o": "fb"}),
            ],
        )
        text = rtlscope.render_diagram(d, d.top, 100)
        drawing = Diagram(text)
        self.assertLess(drawing.box("u_and")["x"], drawing.box("u_inv")["x"])
        self.assertDrawnAsModeled(d, d.top, text)

    def test_inout(self):
        pad = cell_type("pad", ("o", IN), ("io", IO))
        d = design([("pin", IO), ("d", IN)], [], [pad], [("u_pad", "pad", {"o": "d", "io": "pin"})])
        text = rtlscope.render_diagram(d, d.top, 80)
        self.assertIn("◆ pin", text)
        self.assertDrawnAsModeled(d, d.top, text)

    def test_unconnected_and_unsupported_pins(self):
        d = design([("x", IN)], [], [AND2], [("u", "and2", {"a": "x"})])
        d.top.instances["u"].connections["b"] = Unsupported("CONCAT", 3)
        text = rtlscope.render_diagram(d, d.top, 80)
        self.assertIn("?┤b", text)
        # y is unconnected: a plain border, no stub.
        self.assertRegex(text, r"y│")
        self.assertDrawnAsModeled(d, d.top, text)

    def test_leaf_module_is_one_box(self):
        design_ = rtlscope.load_verilator_json(COMBINATIONAL)
        text = rtlscope.render_diagram(design_, design_.modules["mux_procedural"], 80)
        self.assertEqual(
            text.splitlines(),
            [
                "┌────────────────┐",
                "│ mux_procedural │",
                "│sel            y│",
                "│a               │",
                "│b               │",
                "└────────────────┘",
            ],
        )

    def test_pipeline_of_sixteen_instances(self):
        # Four stages of four lanes, each lane also reading its neighbour: a
        # dense crossbar of wires between every pair of columns.
        nets = [f"in{lane}" for lane in range(4)]
        instances = []
        for stage in range(4):
            for lane in range(4):
                prev = (lambda l: f"s{stage - 1}_{l % 4}") if stage else (lambda l: f"in{l % 4}")
                nets.append(f"s{stage}_{lane}")
                instances.append((f"u{stage}{lane}", "and2", {"a": prev(lane), "b": prev(lane + 1), "y": nets[-1]}))
        d = design([], nets, [AND2], instances)
        text = rtlscope.render_diagram(d, d.top, 120)
        drawing = Diagram(text)
        for lane in range(4):
            xs = [drawing.box(f"u{stage}{lane}")["x"] for stage in range(4)]
            self.assertEqual(xs, sorted(set(xs)))
        self.assertDrawnAsModeled(d, d.top, text)

    def test_random_designs_route_correctly(self):
        cells = [BUF, AND2, cell_type("mux", ("s", IN), ("a", IN), ("b", IN), ("y", OUT))]
        for seed in range(12):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                nets = [f"n{i}" for i in range(rng.randint(3, 8))]
                instances = []
                for i in range(rng.randint(2, 7)):
                    kind = rng.choice(cells)
                    conns = {p.name: rng.choice(nets) for p in kind.ports.values() if rng.random() < 0.9}
                    instances.append((f"u{i}", kind.name, conns))
                d = design([("clk", IN), ("out", OUT)], nets, cells, instances)
                d.modules["top"].instances["u0"].connections["i" if instances[0][1] == "buf" else "a"] = NetRef("clk")
                text = rtlscope.render_diagram(d, d.modules["top"], 200)
                self.assertDrawnAsModeled(d, d.modules["top"], text)


class SizeConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = rtlscope.load_verilator_json(COMBINATIONAL)

    def test_too_narrow(self):
        with self.assertRaisesRegex(DiagramError, r"needs at least \d+ columns, but the width is 20"):
            rtlscope.render_diagram(self.design, self.design.top, 20)

    def test_exact_width_fits(self):
        width = max(map(len, COMBINATIONAL_DIAGRAM.splitlines()))
        text = rtlscope.render_diagram(self.design, self.design.top, width)
        self.assertLessEqual(max(map(len, text.splitlines())), width)

    def test_too_short(self):
        with self.assertRaisesRegex(DiagramError, "needs 13 rows, but the height is 10"):
            rtlscope.render_diagram(self.design, self.design.top, 80, height=10)

    def test_height_that_fits(self):
        text = rtlscope.render_diagram(self.design, self.design.top, 80, height=13)
        self.assertEqual(text, COMBINATIONAL_DIAGRAM)


if __name__ == "__main__":
    unittest.main()


class OptimizationGoalTests(DiagramAssertions):
    WIRE = set("─│┌┐└┘├┤┬┴┼")

    @classmethod
    def setUpClass(cls):
        from verilator_snippets import FIXTURES

        cls.counter = rtlscope.load_verilator_json(FIXTURES.parent.parent / "examples" / "Vcounter.tree.json")

    def draw(self, *goals):
        return rtlscope.render_diagram(self.counter, self.counter.top, 100, costs=rtlscope.Costs.favouring(goals))

    def wire_cells(self, text):
        # Box borders use the same glyphs; count only cells outside boxes.
        drawing = Diagram(text)
        return sum(
            1
            for y, row in enumerate(drawing.rows)
            for x, char in enumerate(row)
            if char in self.WIRE and (x, y) not in drawing.box_cells
        )

    def test_costs(self):
        self.assertEqual(rtlscope.Costs.favouring([]), rtlscope.Costs(1, 2, 3))
        self.assertEqual(rtlscope.Costs.favouring(["crossings"]), rtlscope.Costs(1, 2, 15))
        self.assertEqual(rtlscope.Costs.favouring(["crossings", "bends"]), rtlscope.Costs(1, 10, 15))
        with self.assertRaisesRegex(rtlscope.RTLScopeError, "unknown optimization goal 'area'"):
            rtlscope.Costs.favouring(["area"])

    def test_goals_trade_crossings_for_length(self):
        short = self.draw("length")
        clean = self.draw("crossings")
        self.assertEqual(short.count("┼"), 1)
        self.assertEqual(clean.count("┼"), 0)
        self.assertLess(self.wire_cells(short), self.wire_cells(clean))
        for text in (short, clean, self.draw("crossings", "bends")):
            self.assertDrawnAsModeled(self.counter, self.counter.top, text)
