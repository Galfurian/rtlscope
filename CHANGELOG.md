# Changelog

## 0.1.0

The first milestone: a working path from a real Verilator JSON tree to a
normalized connectivity model, and from that model to a block diagram drawn
in the terminal. There is no interactive UI yet.

### Verilator frontend

- Read the `.tree.json` written by `verilator --json-only`: modules, their
  ports and nets, instances, and pin connections.
- Resolve `CELL.modp`, `PIN.modVarp` and `VARREF.varp` as pointers, so neither
  stale module names nor positional pin names can misplace a connection.
- Read each port's and net's declared packed range, following typedefs and
  enums, and its unpacked dimensions for memories, and show them on box
  ports, edge labels and in the text dump: `d[7:0]`, `mem[0:3][7:0]`.
- Show parameterized modules by their source name and elaborated parameter
  values, `bit_reverse #(WIDTH=4)`, instead of Verilator's `bit_reverse__W4`.
- Read every process of a module (`always_ff`, `always_comb`, `always_latch`,
  `always`, `assign`, `initial`, `final`) as the module signals it reads and
  writes, without interpreting its contents.
- Recognize clock ports by the rule synthesis uses, an edge the process body
  never reads, so an asynchronous reset is not mistaken for one. Clocks are
  drawn `┤▷clk` and marked `(clock)` in the text dump.
- Keep pin expressions other than a plain net reference as explicitly
  unsupported, with their kind and source line.
- Reject generate blocks, instance arrays, interface instances, unresolved
  references and doubly connected ports with a clear error, rather than
  dropping them.

### Output

- Draw one module as a box-and-wire block diagram in the terminal, the default
  output. Each box is titled `instance : module`, with its parameter values
  below, and every process is a box of its own, so a flat module is drawn as
  its registers and combinational blocks. Instances are placed in columns that follow the data flow, and
  every net is routed by a maze router on the character grid. Crossings are
  `┼`, branches are `┬ ┴ ├ ┤`, and the two never look alike.
- Edge labels are placed like unconstrained I/O pins: each goes to the row of
  its edge that the cheapest wire reaches, rather than to a row chosen in
  advance.
- `--optimize` favours short wires (`length`), straight ones (`bends`), few
  crossings (`crossings`) or vertical wires kept apart (`spacing`), or a mix
  of them. Spacing is on by default, so a fan-out reads `│ │ │` rather than
  `│││` whenever the width leaves room. The routing is tried in several
  net orders and the drawing that scores best for the chosen goals is kept.
- `--width` and `--height` bound the diagram; one that does not fit is an
  error saying how much room it needs. The width defaults to the terminal.
- `--format text`: a deterministic dump of the whole hierarchy or of one
  module.

### Examples

- `examples/` holds 29 small designs, each as SystemVerilog next to the
  Verilator JSON generated from it: hierarchy, parameters, flat modules,
  state machines, memories, clocking styles, latches, tristates, connection
  styles, pin expressions and a testbench. The test suite draws every module
  of every one and checks the drawing against the model.
