# rtlscope

A terminal-oriented schematic viewer for elaborated RTL designs. `rtlscope`
reads the hierarchy of a SystemVerilog design — module instances, their
ports, and the nets that connect them — and shows it as structure rather than
as source code.

Requires Python 3.10+ and only the standard library.

## Status

Experimental. `rtlscope` reads Verilator's JSON tree into a small
connectivity model and draws one module at a time as a box-and-wire block
diagram in the terminal, or prints it as a deterministic text dump. There is
no interactive UI yet.

## Intended workflow

```text
SystemVerilog ──verilator --json-only──> design.tree.json ──rtlscope──> schematic
```

Generate the tree with Verilator:

```bash
verilator --json-only --top-module tb_combinational design.sv
# writes obj_dir/Vtb_combinational.tree.json (and .tree.meta.json)
```

`rtlscope` reads the `.tree.json` file. The `.tree.meta.json` file is not
needed.

## Commands

```bash
python3 rtlscope.py design.tree.json                     # top module, as a block diagram
python3 rtlscope.py design.tree.json --module NAME       # another module
python3 rtlscope.py design.tree.json --width 120         # at most 120 columns wide
python3 rtlscope.py design.tree.json --height 40         # ... and at most 40 rows tall
python3 rtlscope.py design.tree.json --optimize crossings # fewer crossings, longer wires
python3 rtlscope.py design.tree.json --format text       # whole hierarchy, as text
```

`--optimize` steers the routing towards `length` (short wires), `bends`
(straight wires) or `crossings` (few crossings). Name several, separated by
commas, to mix them: `--optimize crossings,bends`.

The diagram defaults to the terminal width. A module that cannot be drawn
within `--width`, or within `--height` when given, is an error that says how
much room it needs.

Errors are reported as `rtlscope: error: ...` with exit status 2. `--debug`
shows the traceback instead.

## Example

`tests/fixtures/Vtb_combinational.tree.json` is a real Verilator tree of a
testbench that instantiates two 2:1 multiplexers, one written with
`always_comb` and one with `assign`:

```text
$ python3 rtlscope.py tests/fixtures/Vtb_combinational.tree.json
            ┌──────────────┐
            │    u_proc    │
            │mux_procedural│
sel ─────┬──┤sel          y├─────── y_proc
a ──────┬┼──┤a             │
b ─────┬┼┼──┤b             │
       │││  └──────────────┘
       │││
       │││  ┌──────────────┐
       │││  │    u_cont    │
       │││  │mux_continuous│
       ││└──┤sel          y├─────── y_cont
       │└───┤a             │
       └────┤b             │
            └──────────────┘
```

Reading the diagram:

```text
┌────┐               an instance: instance name, then module name
┤a  y├               inputs on the left edge; outputs and inouts on the right
d[7:0]               a bus, with its declared range
sel ──   ── y_proc   a net driven, or read, by logic that is not drawn
clk ▶  ▶ q  ◆ io     ports of the module being drawn: input, output, inout
┬ ┴ ├ ┤              a net branches
┼                    two different nets cross; never a connection
?┤                   connected to an expression rtlscope cannot show yet
```

Only structure is extracted. The multiplexers' `always_comb` and `assign`
bodies, and the testbench's `initial` block, are not drawn.

`--format text` prints the same connectivity as a list, and shows what an
unsupported pin is connected to, for example `input  sel <- ? SEL (line 37)`.

## Examples

`examples/` holds small SystemVerilog designs, each next to the Verilator JSON
generated from it, so they can be viewed without Verilator:

```bash
python3 rtlscope.py examples/Vfull_adder.tree.json   # two half adders and an OR gate
python3 rtlscope.py examples/Vcounter.tree.json      # a register fed back through an incrementer
python3 rtlscope.py examples/Vpipeline.tree.json     # a two-stage pipelined adder
```

Regenerating them needs Verilator 5.022 or newer, for `--json-only`; the
committed files come from Verilator 5.052:

```bash
for f in examples/*.sv; do verilator --json-only --Mdir examples "$f"; done
```

## Development

```bash
python3 -m unittest discover -s tests -v
```

The diagram tests read every drawing back character by character and check
that its wires connect exactly the pins the model says they do.

See `DESIGN.md` for the architecture and how the Verilator tree is read, and
`CHANGELOG.md` for what changed between releases.
