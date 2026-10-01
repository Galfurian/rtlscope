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
- Keep pin expressions other than a plain net reference as explicitly
  unsupported, with their kind and source line.
- Reject generate blocks, instance arrays, interface instances, unresolved
  references and doubly connected ports with a clear error, rather than
  dropping them.

### Output

- Draw one module as a box-and-wire block diagram in the terminal, the default
  output. Instances are placed in columns that follow the data flow, and
  every net is routed by a maze router on the character grid. Crossings are
  `┼`, branches are `┬ ┴ ├ ┤`, and the two never look alike.
- `--width` and `--height` bound the diagram; one that does not fit is an
  error saying how much room it needs. The width defaults to the terminal.
- `--format text`: a deterministic dump of the whole hierarchy or of one
  module.

### Examples

- `examples/` holds a full adder, a counter and a pipelined adder, each as
  SystemVerilog next to the Verilator JSON generated from it. The test suite
  draws every one and checks the drawing against the model.
