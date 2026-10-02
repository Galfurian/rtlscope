# rtlscope design

## Purpose

`rtlscope` is for exploring the hierarchy of an elaborated RTL design from a
terminal: which modules a design instantiates, which ports each instance has,
and which nets connect them. Its eventual form is an interactive box-and-wire
schematic that can be entered and left one level of hierarchy at a time.

The project values a small, explainable implementation over breadth, in the
same spirit as `vcdtui`.

## Baseline contract

- Python 3.10 or newer.
- Python standard library only at runtime.
- A single file, runnable as `python3 rtlscope.py design.tree.json`.
- Unsupported input fails deliberately, or is marked as unsupported in the
  model. It is never silently turned into wrong connectivity.
- Expected failures are one line on stderr with exit status 2. `--debug` shows
  the traceback.

## Architectural boundary

```text
Verilator .tree.json
        |
        v
Verilator frontend          resolves addr pointers, rejects what it cannot model
        |
        v
normalized model            Design, Module, Port, Instance, NetRef, Unsupported
        |
        +--> block diagram  (render_diagram)
        +--> text dump      (render_text)
        +--> TUI            (future)
```

The model holds names, not Verilator nodes. Verilator's `addr` identifiers
(`(M)`, `(F)`, ...) are resolved inside the frontend and never reach the
model, so a renderer cannot depend on them, and a second frontend only has to
produce the same few classes.

`rtlscope.py` is one file with banner sections for the model, the frontend,
the renderers and the command line. That is enough for this milestone; the
sections can become modules when one of them outgrows the file.

## Internal model

```text
Design
  modules: name -> Module          declaration order
  tops                             modules nobody instantiates
  top                              the only top, or an error naming them all

Module
  name                             unique: Verilator's "bit_reverse__W4"
  source                           the name in the source: "bit_reverse"
  params: name -> value            overridable parameters, as elaborated
  signature                        "bit_reverse #(WIDTH=4)"
  ports: name -> Port              declaration order
  nets: name -> Net                module-level signals that are not ports
  instances: name -> Instance      source order
  processes: [Process]             source order

Port
  name
  direction: input | output | inout
  bits: Bits | None                declared packed range; None for one bit

Net
  name
  bits: Bits | None

Bits
  msb, lsb                         as declared, so [0:7] stays [0:7]
  width

Instance
  name
  module                           name of the instantiated Module
  connections: port name -> Expr   a missing port is unconnected

Expr = NetRef(net) | Unsupported(kind, line)

Process                            seen from outside only
  kind                             always_ff | always_comb | always_latch |
                                   always | assign | initial | final
  line
  reads, writes, clocks            module signals, in declaration order
  name                             "always_ff, line 19"
```

`NetRef.net` names a port or a net of the instance's parent module. A `Net`
holds what belongs to the signal itself, its name and width, and nothing
about what it connects: which pins drive or read it is derived from the
instances' `connections` whenever a renderer needs it. Keeping that in one
place means it cannot disagree with itself.

An instance does not repeat its module's ports. A renderer draws every port of
`design.modules[inst.module]`, so an omitted pin and a pin connected to
nothing (`.y()`) both show as unconnected, which is what they are.

`Unsupported` keeps the expression's kind and source line. It records that the
port is connected to something, without claiming to know to what.

## Initial frontend: Verilator JSON

The input is the `.tree.json` file written by `verilator --json-only`. It is
Verilator's AST after linking and parameter elaboration: every reference has
already been resolved to the node it refers to, and each node is identified by
an `addr` string. The companion `.tree.meta.json` maps the file letters used
in `loc` fields to file names; `rtlscope` does not need it.

Verilator does not promise the JSON layout is stable across versions, so the
frontend reads only the fields listed below and checks the shapes it relies on.
The fixture in `tests/fixtures/` does not record which Verilator version wrote
it.

### Structural extraction

The relevant part of the tree is:

```text
NETLIST
  modulesp[]
    MODULE                 name
      stmtsp[]
        VAR                name, varType, direction
        CELL               name, modp
          pinsp[]
            PIN            modVarp
              exprp[]
                VARREF     varp
```

The frontend indexes every node by `addr` and then makes two passes over the
`MODULE` nodes in `modulesp`. Two passes are needed because a `CELL` may refer
to a module declared after its parent.

1. **Signals.** Each `VAR` directly in a module's `stmtsp` is a port when its
   `direction` is `INPUT`, `OUTPUT` or `INOUT`, and a net when it is `NONE`.
   Parameters (`varType` `GPARAM`, `LPARAM`, `GENVAR`) are neither. Any other
   direction, such as `REF`, is an error. Variables declared inside blocks,
   like a loop index in an `initial`, are not module-level and are ignored.
   The width comes from `VAR.dtypep`:
   a `BASICDTYPE` has a `range` such as `"7:0"`, or none for a single bit,
   and a typedef (`REFDTYPE`) or an enum (`ENUMDTYPE`) is followed through
   `refDTypep` to the type it is built on. Other types, such as
   multi-dimensional packed arrays and structs, are errors for now.

   A module elaborated with non-default parameters is a `MODULE` of its own
   with a mangled `name` such as `bit_reverse__W4`; its `origName` is the
   source name. Each `VAR` with `varType` `GPARAM` is an overridable
   parameter, whose `valuep` holds the elaborated `CONST`, written by
   Verilator as `32'sh4` and shown as `4`. Local parameters (`LPARAM`) are
   not shown: nobody chose their value at the instantiation.
2. **Instances.** Each `CELL` directly in `stmtsp` becomes an `Instance`:
   - `CELL.modp` points at the instantiated `MODULE`. Its `name` is used, not
     the `CELL.modName` string.
   - `PIN.modVarp` points at a port `VAR` of that child module. That port, not
     `PIN.name`, determines which port is connected and in which direction.
     Positional connections have synthetic pin names, so `PIN.name` cannot be
     trusted.
   - `PIN.exprp` holds the connected expression. An empty list is an explicit
     non-connection.
   - A `VARREF` there has `varp` pointing at a `VAR` of the parent module,
     which becomes a `NetRef` to that signal. Again the pointer decides, not
     `VARREF.name`.
3. **Processes.** Each `ALWAYS` (with its `keyword`: `always_ff`,
   `always_comb`, `always_latch`, `always`, or `cont_assign` for an
   `assign`), `INITIAL` and `FINAL` directly in `stmtsp` becomes a `Process`.
   Its signals are the `VARREF`s inside it that point at a module-level port
   or net: `access` `RD` is a read, `WR` a write, `RW` both. References to
   variables declared inside the process, like a loop index, are left out.

   A clock is an edge in the sensitivity list (`SENITEM` with `edgeType`
   `POS`, `NEG` or `BOTH`) that the body never reads. That is how synthesis
   tells a clock from an asynchronous reset: in
   `always_ff @(posedge clk or negedge rst_n)` both are edges, but the body
   tests `rst_n` and never `clk`. Names play no part, so `clk_en` is not a
   clock and `aclk` is. A port that clocks a process, or is wired to a
   child's clock port, is marked as a clock, so the marking propagates up the
   hierarchy.

A process is never looked inside beyond which signals it refers to. Its
statements and expressions are not interpreted, so a process is one box, not
the gates it would synthesize to.

### Pin expressions

The frontend reduces a pin expression in one function, `_read_expr`. Today it
understands a `VARREF` to a port or net of the parent module. Everything else
is returned as `Unsupported(kind, line)`:

```text
VARREF to a port or net    -> NetRef(name)
VARREF to a parameter      -> Unsupported("VARREF to GPARAM", line)
SEL, CONCAT, CONST, COND,
operators, ...             -> Unsupported(node type, line)
```

Supporting bus slices and concatenations means extending the model's `Expr`
and that one function, not the traversal.

### Rejected input

These are errors, because ignoring them would lose or misplace structure:

- a root node that is not `NETLIST`, or a file that is not JSON;
- a `modp`, `modVarp` or `varp` that is `UNLINKED` or points nowhere;
- a `modVarp` that is not a port of the instantiated module;
- a `varp` that points into a module other than the parent;
- the same port connected twice;
- a `CELL` nested anywhere other than directly in a module (generate blocks);
- instance arrays (`CELL.rangep`), interface references, and instances of
  anything that is not a `MODULE`, such as an `IFACE`.

Error messages name the node type, its name and its source line.

## Renderers

### Text

`render_text` prints each module's ports, nets and instances, and for each
instance every port of the instantiated module with its connection:

```text
    input  sel <- sel
    output y   -> y_proc
    inout  io  <-> pad
    output z   (unconnected)
    input  d   <- ? SEL (line 37)
```

Without `--module` it prints every module, each top followed depth-first by
the modules it instantiates. It is a debugging and test aid, not the final UI.

### Block diagram

`render_diagram` draws one module on a character grid. It works like a very
small physical design flow, in four steps: plan, place, route, check.

**Plan.** Every process becomes a box titled by its kind and line,
`always_ff, line 19`, whose input pins are the signals it reads (clocks
first) and whose output pins are those it writes, each pin named after its
signal. A flat module with no instances is therefore still a circuit:
registers, combinational blocks and the wires between them. Only a module
with neither instances nor processes is drawn as a single box.

Every instance becomes a box titled `u4 : bit_reverse`, the same
convention as the text dump, with its parameter values on a second line,
`#(WIDTH=4)`. Parameters are shown for every specialization, defaults
included, so two instances of one module visibly differ only in them.
Verilator's mangled names never appear in a diagram. Input ports go on the
left edge, outputs and inouts on the right; a clock input carries the
flip-flop symbol's triangle, `┤▷clk`. Every net
becomes a list of ends: the box pins on it, plus labels at the edges of the
diagram where the net leaves what is drawn:

```text
module input port            left label   "clk ▶"
module output / inout port   right label  "▶ q", "◆ io"
internal net nobody drives   left label   "sel"   (behavioral logic drives it)
internal net nobody reads    right label  "y_proc"
```

**Place.** Instances are layered into columns by their longest path from the
inputs, so data flows left to right. Cycles are cut where a forward walk from
the inputs first closes them, so a feedback wire is the one that runs back.
Within a column, instances are ordered by the barycenter of the instances
that drive them, which removes most needless crossings. Boxes are stacked and
centered in their column, with a free row above and below the whole
placement so that wires can pass over the boxes.

Columns are separated by routing channels. A channel gets one track for each
net that crosses it, plus room for the stub and access cell of the pins on
both sides. If the boxes and minimum channels do not fit in the width, that is
the error.

**Route.** Each net is routed as a tree with a maze router: Dijkstra over
(cell, heading), with a cost for every step, every bend and every crossing.
The first end seeds the tree; every other end is connected, nearest first, to
the closest point of what is already routed. The rules keep the drawing
unambiguous:

- a wire crosses another net only at a right angle, through a straight
  segment, and leaves the crossing straight. That cell is drawn `┼`, so `┼`
  never means a connection;
- a net joins itself only where the result is a three-way branch (`┬ ┴ ├ ┤`),
  never a four-way one;
- a ring of cells around each box stays empty, so no wire runs along a border
  and reads as part of it;
- each pin owns its stub and the access cell beyond it. Nothing crosses them,
  so no pin can be walled in.

Routing nets one after the other can still let early nets box in a later one.
When a net fails, everything is torn up and that net goes first (rip-up and
reroute), until an order succeeds or every order tried has failed. If none
works, the whole diagram is placed again with wider channels and more space
between boxes, up to a fixed number of attempts.

Labels have no row until they are routed. Like an unconstrained I/O pin in a
physical design flow, a label is routed from every free row of its edge at
once, and it goes where the cheapest wire reaches the edge. Nothing decides
by hand when a wire may run straight to an edge: a straight wire wins when one
exists because it has no bends and no crossings. Among equally cheap rows, the
one nearest the net's first pin wins, so the result is deterministic. Ports
are therefore ordered along an edge by what suits the wiring, not by
declaration order.

**Optimize.** The costs are the objective: by default 1 per wire cell, 2 per
bend, 3 per crossing, so a crossing is worth three cells of detour, and 1 per
cell where a vertical wire runs right beside another net's. Only vertical
runs count for spacing: wires into consecutive pins of a box are one row
apart by construction, while tracks in a channel can be spread. When the
width allows it, channels are sized with a free column between tracks so
there is room to spread them; otherwise they fall back to one column per
track. `--optimize` names goals among `length`, `bends`, `crossings` and
`spacing`; each named
goal multiplies its cost by 5, and naming several mixes them, the way a
synthesis run trades area against delay.

Costs alone do not make the drawing optimal, because nets are routed one at
a time and each one only sees those already routed. In the counter example,
`count` is routed before `next` exists; whatever its cost, the crossing is
paid later by `next`. So the routing is repeated for a few net orders
(declaration order, reversed, by fan-out, by span both ways), each finished
drawing is scored with the same costs over all its wire cells, bends and
crossings, and the lowest score wins, the first order on ties.

**Check.** Empty margins are trimmed. A diagram wider than `width`, or taller
than `height` when given, is an error naming the size it needs.

Unconnected ports keep a plain border. An `Unsupported` connection is drawn as
a `?` stub, since the diagram cannot say what it is connected to.

The tests do not trust the router: `tests/diagram_trace.py` reads a rendered
diagram back character by character, finds the boxes, follows every wire
through branches and crossings, and the tests compare the result with the
model's connectivity. This is checked on the real fixture, on every design in
`examples/`, on hand-made designs (chains, fan-out, skip connections,
feedback, inouts, a 16-instance pipeline) and on randomly wired ones.

## Non-goals for the first milestone

- No behavioral AST viewer: a process is one box, drawn by the signals it
  reads and writes; its statements and expressions are not shown, and it is
  not broken down into gates.
- No simulation.
- No waveform viewer; that is `vcdtui`'s job.
- No synthesis or logic optimization.
- No complete SystemVerilog expression evaluator.
- No interactive TUI yet.

## Future work

- Interactive navigation: entering an instance, leaving to the parent.
- Folding a module that is too wide for the terminal into several rows of
  columns, so a width alone is always enough.
- Net names on internal wires, and an `--ascii` mode.
- Selecting a net and highlighting every pin on it.
- Net and port widths, then bus slices (`SEL`) and concatenations (`CONCAT`)
  on pins, which turn a connection into a list of bit ranges.
- Constants on pins.
- Richer expressions, shown as anonymous logic rather than evaluated.
- Generate blocks and instance arrays.
- Other frontends, such as Yosys JSON or slang, producing the same model.
