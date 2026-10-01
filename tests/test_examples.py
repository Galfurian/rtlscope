"""Every example in examples/ is read and drawn as the source describes it."""

import unittest
from pathlib import Path

import rtlscope
from rtlscope import NetRef
from diagram_trace import Diagram
from test_diagram import DiagramAssertions

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class ExampleTests(DiagramAssertions):
    def test_every_example_is_drawn_as_modeled(self):
        trees = sorted(EXAMPLES.glob("*.tree.json"))
        self.assertTrue(trees)
        for tree in trees:
            with self.subTest(example=tree.name):
                design = rtlscope.load_verilator_json(tree)
                text = rtlscope.render_diagram(design, design.top, 120)
                self.assertDrawnAsModeled(design, design.top, text)

    def test_every_source_has_its_tree(self):
        for source in sorted(EXAMPLES.glob("*.sv")):
            with self.subTest(example=source.name):
                self.assertTrue((EXAMPLES / f"V{source.stem}.tree.json").is_file())

    def test_full_adder(self):
        design = rtlscope.load_verilator_json(EXAMPLES / "Vfull_adder.tree.json")
        top = design.top
        self.assertEqual(top.name, "full_adder")
        self.assertEqual(
            {name: (inst.module, inst.connections) for name, inst in top.instances.items()},
            {
                "u_ha1": ("half_adder", {"a": NetRef("a"), "b": NetRef("b"), "s": NetRef("s1"), "c": NetRef("c1")}),
                "u_ha2": ("half_adder", {"a": NetRef("s1"), "b": NetRef("cin"), "s": NetRef("s"), "c": NetRef("c2")}),
                "u_or": ("or2", {"a": NetRef("c1"), "b": NetRef("c2"), "y": NetRef("cout")}),
            },
        )

    def test_counter_feedback(self):
        design = rtlscope.load_verilator_json(EXAMPLES / "Vcounter.tree.json")
        top = design.top
        self.assertEqual(top.instances["u_reg"].connections["d"], NetRef("next"))
        self.assertEqual(top.instances["u_inc"].connections["y"], NetRef("next"))
        # The register comes first; the loop back through the incrementer is the feedback.
        drawing = Diagram(rtlscope.render_diagram(design, top, 120))
        self.assertLess(drawing.box("u_reg")["x"], drawing.box("u_inc")["x"])


if __name__ == "__main__":
    unittest.main()
