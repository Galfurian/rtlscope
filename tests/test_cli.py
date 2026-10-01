import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import rtlscope
from test_diagram import COMBINATIONAL_DIAGRAM
from verilator_snippets import COMBINATIONAL

EXPECTED_TOP = """\
module tb_combinational
  net    sel
  net    a
  net    b
  net    y_proc
  net    y_cont
  instance u_proc : mux_procedural
    input  sel <- sel
    input  a   <- a
    input  b   <- b
    output y   -> y_proc
  instance u_cont : mux_continuous
    input  sel <- sel
    input  a   <- a
    input  b   <- b
    output y   -> y_cont
"""


def run_main(*args):
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = rtlscope.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


class CLITests(unittest.TestCase):
    def test_diagram_is_default(self):
        code, out, err = run_main(COMBINATIONAL, "--width", "80")
        self.assertEqual((code, out, err), (0, COMBINATIONAL_DIAGRAM, ""))

    def test_diagram_of_child_module(self):
        code, out, err = run_main(COMBINATIONAL, "--module", "mux_continuous", "--width", "80")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[1], "│mux_continuous│")

    def test_diagram_too_narrow_or_short(self):
        for args, message in (
            (("--width", "20"), "needs at least"),
            (("--width", "80", "--height", "5"), "needs 15 rows, but the height is 5"),
        ):
            with self.subTest(args=args):
                code, out, err = run_main(COMBINATIONAL, *args)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("rtlscope: error: diagram"), err)
                self.assertIn(message, err)

    def test_size_must_be_positive(self):
        for value in ("0", "-3", "wide"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
                    rtlscope.main([str(COMBINATIONAL), "--width", value])
                self.assertIn("expected a positive integer", err.getvalue())

    def test_text_whole_hierarchy(self):
        code, out, err = run_main(COMBINATIONAL, "--format", "text")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(EXPECTED_TOP + "\nmodule mux_procedural\n"))
        self.assertIn("\nmodule mux_continuous\n", out)

    def test_text_for_one_module(self):
        code, out, err = run_main(COMBINATIONAL, "--format", "text", "--module", "tb_combinational")
        self.assertEqual((code, out, err), (0, EXPECTED_TOP, ""))

    def test_unknown_module(self):
        code, out, err = run_main(COMBINATIONAL, "--module", "nope")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(
            err,
            "rtlscope: error: no module named 'nope'; known modules: "
            "tb_combinational, mux_procedural, mux_continuous\n",
        )

    def test_clean_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            not_tree = Path(tmp) / "meta.json"
            not_tree.write_text('{"files": {}}', encoding="utf-8")
            cases = {
                bad_json: "invalid JSON",
                not_tree: "not a Verilator JSON tree",
                Path(tmp) / "missing.json": "cannot read",
            }
            for path, message in cases.items():
                with self.subTest(path=path.name):
                    code, out, err = run_main(path)
                    self.assertEqual((code, out), (2, ""))
                    self.assertTrue(err.startswith("rtlscope: error: "), err)
                    self.assertIn(message, err)
                    self.assertNotIn("Traceback", err)

    def test_debug_shows_traceback(self):
        code, _, err = run_main(COMBINATIONAL, "--module", "nope", "--debug")
        self.assertEqual(code, 2)
        self.assertIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
