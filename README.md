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
(straight wires), `crossings` (few crossings) or `spacing` (vertical wires
kept apart). Name several, separated by commas, to mix them:
`--optimize crossings,bends`.

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
                             ┌─────────────────────────┐
                             │ u_proc : mux_procedural │
                     ┌────┬──┤sel                     y├──────── y_proc
┌──────────────────┐ │ ┌──┼──┤a                        │
│ initial, line 40 │ │ │  │┌─┤b                        │
│               sel├─┘ │  ││ └─────────────────────────┘
│                 a├───┤  ││
│                 b├─┬─┼──┼┘ ┌─────────────────────────┐
└──────────────────┘ │ │  │  │ u_cont : mux_continuous │
                     │ │  └──┤sel                     y├──────── y_cont
                     │ └─────┤a                        │
                     └───────┤b                        │
                             └─────────────────────────┘
```

Reading the diagram:

```text
│ u4 : rev │         an instance, then the module it instantiates,
│ #(W=4)   │         with the parameter values it was elaborated with
always_ff, line 9    a process box: its kind and where it starts in the source
┤a  y├               inputs on the left edge; outputs and inouts on the right
d[7:0]               a bus, with its declared range
┤▷clk                a clock input
sel ──   ── y_proc   a net driven or read by nothing drawn in this module
clk ▶  ▶ q  ◆ io     ports of the module being drawn: input, output, inout
┬ ┴ ├ ┤              a net branches
┼                    two different nets cross; never a connection
?┤                   connected to an expression rtlscope cannot show yet
```

Each process is a box too: here the testbench's `initial` block, which drives
`sel`, `a` and `b`. A process is drawn by the signals it reads and writes, not
by what it computes, so a flat module with no instances is still a circuit of
registers and combinational blocks:

```text
$ python3 rtlscope.py examples/Vtimer.tree.json
                                                   ┌──────────────────────────────────▶ value[3:0]
                                                   │
                                                   │ ┌──────────────────────┐
                                                   │ │ always_comb, line 15 │
                  ┌─────────────────────┐ ┌───────┬┴─┤value[3:0]   next[3:0]├─┐
                  │  always_ff, line 19 │ │ ┌─────┼──┤running               │ │
clk ▶─────────────┤▷clk       value[3:0]├─┘ │     │  └──────────────────────┘ │
rst_n ▶───────────┤rst_n         running├───┘     │                           │
start ▶───────────┤start                │ ┌───────┼───────────────────────────┘
              ┌───┤done                 │ │       │
              │ ┌─┤next[3:0]            │ │       │    ┌─────────────────┐
              │ │ └─────────────────────┘ │       │    │ assign, line 30 │
              │ │                         │       └────┤value[3:0]   done├─┬──────────▶ done
              │ └─────────────────────────┘            └─────────────────┘ │
              │                                                            │
              └────────────────────────────────────────────────────────────┘
```

`--format text` prints the same connectivity as a list, and shows what an
unsupported pin is connected to, for example `input  sel <- ? SEL (line 37)`.

## Examples

`examples/` holds small SystemVerilog designs, each next to the Verilator JSON
generated from it, so they can be viewed without Verilator:

```bash
python3 rtlscope.py examples/Vfull_adder.tree.json   # two half adders and an OR gate
python3 rtlscope.py examples/Vcounter.tree.json      # a register fed back through an incrementer
python3 rtlscope.py examples/Vpipeline.tree.json     # a two-stage pipelined adder
python3 rtlscope.py examples/Vparameters.tree.json   # one module at two parameter values
python3 rtlscope.py examples/Vshift_register.tree.json  # flip-flops with an asynchronous reset
python3 rtlscope.py examples/Vtimer.tree.json        # a flat module: a register and combinational logic
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
