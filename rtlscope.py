#!/usr/bin/env python3
"""rtlscope: explore the structure of an elaborated RTL design."""

from __future__ import annotations

import argparse
import heapq
import json
import os
import shutil
import sys
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

__version__ = "0.1.0"


class RTLScopeError(Exception):
    """An expected failure, reported to the user without a traceback."""


class VerilatorJSONError(RTLScopeError):
    """The Verilator JSON does not describe a design rtlscope can read."""


# ---------------------------------------------------------------------------
# Normalized connectivity model
#
# Nothing below this banner knows about Verilator. Renderers only see these
# classes, so a second frontend has to produce them and nothing else.
# ---------------------------------------------------------------------------


class Direction(Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


@dataclass(frozen=True)
class Bits:
    """A packed range as declared, ``[msb:lsb]``; either end may be the larger."""

    msb: int
    lsb: int

    @property
    def width(self) -> int:
        return abs(self.msb - self.lsb) + 1

    def __str__(self) -> str:
        return f"[{self.msb}:{self.lsb}]"


@dataclass(frozen=True)
class Port:
    name: str
    direction: Direction
    bits: Optional[Bits] = None  # None for a single bit

    @property
    def display(self) -> str:
        return self.name + (str(self.bits) if self.bits else "")


@dataclass(frozen=True)
class Net:
    """A module-level signal that is not a port."""

    name: str
    bits: Optional[Bits] = None  # None for a single bit

    @property
    def display(self) -> str:
        return self.name + (str(self.bits) if self.bits else "")


@dataclass(frozen=True)
class NetRef:
    """A pin connected to a whole net of the parent module."""

    net: str


@dataclass(frozen=True)
class Unsupported:
    """A pin expression that rtlscope cannot turn into connectivity yet.

    It is kept in the model rather than dropped, so a renderer can show that
    something is connected there without pretending to know what.
    """

    kind: str
    line: Optional[int] = None


Expr = Union[NetRef, Unsupported]


@dataclass
class Instance:
    name: str
    module: str
    # Child port name -> expression in the parent module. A port missing from
    # this mapping is unconnected.
    connections: Dict[str, Expr] = field(default_factory=dict)


@dataclass
class Module:
    name: str
    ports: Dict[str, Port] = field(default_factory=dict)
    # Module-level signals that are not ports, in declaration order.
    nets: Dict[str, Net] = field(default_factory=dict)
    instances: Dict[str, Instance] = field(default_factory=dict)


@dataclass
class Design:
    modules: Dict[str, Module]

    @property
    def tops(self) -> List[str]:
        """Modules that no other module instantiates, in declaration order."""
        used = {inst.module for module in self.modules.values() for inst in module.instances.values()}
        return [name for name in self.modules if name not in used]

    @property
    def top(self) -> Module:
        tops = self.tops
        if len(tops) != 1:
            names = ", ".join(tops) or "none"
            raise RTLScopeError(f"expected exactly one top module, found {names}; choose one with --module")
        return self.modules[tops[0]]

    def module(self, name: str) -> Module:
        try:
            return self.modules[name]
        except KeyError:
            known = ", ".join(self.modules)
            raise RTLScopeError(f"no module named {name!r}; known modules: {known}") from None

    def hierarchy(self) -> List[Module]:
        """Every module, each top followed depth-first by what it instantiates."""
        order: List[Module] = []
        seen = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            module = self.modules[name]
            order.append(module)
            for inst in module.instances.values():
                visit(inst.module)

        for name in self.tops:
            visit(name)
        # Only a cycle leaves a module unreached; list it rather than lose it.
        for name in self.modules:
            visit(name)
        return order


# ---------------------------------------------------------------------------
# Verilator frontend
# ---------------------------------------------------------------------------

_DIRECTIONS = {"INPUT": Direction.INPUT, "OUTPUT": Direction.OUTPUT, "INOUT": Direction.INOUT}
_NON_NET_VAR_TYPES = {"GPARAM", "LPARAM", "GENVAR"}
_UNLINKED = "UNLINKED"


def load_verilator_json(path: Path) -> Design:
    """Read a Verilator ``--json-only`` tree file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RTLScopeError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerilatorJSONError(f"{path}: invalid JSON: {exc}") from exc
    return parse_verilator_tree(data)


def parse_verilator_tree(data: Any) -> Design:
    """Build the normalized model from a decoded Verilator JSON tree."""
    if not isinstance(data, dict) or data.get("type") != "NETLIST":
        raise VerilatorJSONError("not a Verilator JSON tree: the root node is not a NETLIST")
    return _VerilatorReader(data).read()


def _walk(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield every AST node below and including ``node``."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "type" in item:
                yield item
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _line(node: Dict[str, Any]) -> Optional[int]:
    """The first source line of a node, from a ``loc`` such as ``e,37:27,37:30``."""
    try:
        return int(node["loc"].split(",")[1].split(":")[0])
    except (KeyError, IndexError, ValueError, AttributeError):
        return None


def _describe(node: Dict[str, Any]) -> str:
    text = f"{node.get('type', '?')} {node.get('name', '')!r}"
    line = _line(node)
    return f"{text} (line {line})" if line is not None else text


class _VerilatorReader:
    """Resolves Verilator's pointer-like ``addr`` references into the model."""

    def __init__(self, netlist: Dict[str, Any]) -> None:
        self.netlist = netlist
        self.nodes = {node["addr"]: node for node in _walk(netlist) if "addr" in node}
        # Variable addr -> (owning module, signal name), for ports and nets alike.
        self.signals: Dict[str, Tuple[str, str]] = {}
        self.ports: Dict[str, Port] = {}

    def read(self) -> Design:
        module_nodes = [n for n in self.netlist.get("modulesp", []) if n.get("type") == "MODULE"]
        modules: Dict[str, Module] = {}
        # Two passes: a CELL may point at a module declared after its parent.
        for node in module_nodes:
            modules[node["name"]] = self._read_signals(node)
        for node in module_nodes:
            self._read_instances(node, modules)
        return Design(modules)

    def _resolve(self, node: Dict[str, Any], key: str) -> Dict[str, Any]:
        addr = node.get(key)
        target = self.nodes.get(addr) if addr not in (None, _UNLINKED) else None
        if target is None:
            raise VerilatorJSONError(f"{_describe(node)}: unresolved {key} ({addr})")
        return target

    def _bits(self, var: Dict[str, Any]) -> Optional[Bits]:
        """The packed range of a variable, following typedef references."""
        dtype = self._resolve(var, "dtypep")
        for _ in range(16):
            if dtype.get("type") == "BASICDTYPE":
                if "range" not in dtype:
                    return None
                msb, _, lsb = dtype["range"].partition(":")
                try:
                    return Bits(int(msb), int(lsb))
                except ValueError:
                    raise VerilatorJSONError(f"{_describe(var)}: unreadable range {dtype['range']!r}") from None
            # A typedef and an enum are as wide as the type they refer to.
            if dtype.get("type") not in ("REFDTYPE", "ENUMDTYPE"):
                break
            dtype = self._resolve(dtype, "refDTypep")
        kind = {"PACKARRAYDTYPE": "multi-dimensional packed arrays"}.get(dtype.get("type"), f"data type {dtype.get('type')}")
        raise VerilatorJSONError(f"{_describe(var)}: {kind} not supported yet")

    def _read_signals(self, node: Dict[str, Any]) -> Module:
        module = Module(node["name"])
        for stmt in node.get("stmtsp", []):
            if stmt.get("type") != "VAR":
                continue
            name = stmt["name"]
            direction = stmt.get("direction", "NONE")
            if direction in _DIRECTIONS:
                port = Port(name, _DIRECTIONS[direction], self._bits(stmt))
                module.ports[name] = port
                self.ports[stmt["addr"]] = port
            elif direction != "NONE":
                raise VerilatorJSONError(f"{_describe(stmt)}: unsupported port direction {direction}")
            elif stmt.get("varType") in _NON_NET_VAR_TYPES:
                continue
            else:
                module.nets[name] = Net(name, self._bits(stmt))
            self.signals[stmt["addr"]] = (module.name, name)
        return module

    def _read_instances(self, node: Dict[str, Any], modules: Dict[str, Module]) -> None:
        parent = modules[node["name"]]
        stmts = node.get("stmtsp", [])
        for stmt in stmts:
            if stmt.get("type") != "CELL":
                # Instances under generate blocks would silently vanish here.
                for nested in _walk(stmt):
                    if nested.get("type") == "CELL":
                        raise VerilatorJSONError(
                            f"{_describe(nested)}: instances inside {stmt.get('type')} "
                            "blocks are not supported yet"
                        )
                continue
            inst = self._read_cell(stmt, parent, modules)
            parent.instances[inst.name] = inst

    def _read_cell(self, cell: Dict[str, Any], parent: Module, modules: Dict[str, Module]) -> Instance:
        target = self._resolve(cell, "modp")
        if target.get("type") != "MODULE":
            raise VerilatorJSONError(f"{_describe(cell)}: instances of {target.get('type')} are not supported yet")
        if cell.get("rangep"):
            raise VerilatorJSONError(f"{_describe(cell)}: instance arrays are not supported yet")
        if cell.get("intfRefsp"):
            raise VerilatorJSONError(f"{_describe(cell)}: interface references are not supported yet")
        child = modules[target["name"]]
        inst = Instance(cell["name"], child.name)
        for pin in cell.get("pinsp", []):
            port, expr = self._read_pin(pin, parent, child)
            if port in inst.connections:
                raise VerilatorJSONError(f"{_describe(pin)}: port {port} of {inst.name} is connected twice")
            if expr is not None:
                inst.connections[port] = expr
        return inst

    def _read_pin(self, pin: Dict[str, Any], parent: Module, child: Module) -> Tuple[str, Optional[Expr]]:
        var = self._resolve(pin, "modVarp")
        if self.signals.get(var.get("addr"), (None,))[0] != child.name or var["addr"] not in self.ports:
            raise VerilatorJSONError(f"{_describe(pin)}: modVarp is not a port of {child.name}")
        port = self.ports[var["addr"]]
        exprs = pin.get("exprp", [])
        if not exprs:
            return port.name, None
        if len(exprs) != 1:
            raise VerilatorJSONError(f"{_describe(pin)}: expected one expression, found {len(exprs)}")
        return port.name, self._read_expr(exprs[0], parent)

    def _read_expr(self, node: Dict[str, Any], parent: Module) -> Expr:
        # The one place that interprets pin expressions: SEL, CONCAT, CONST and
        # friends land here when they become supported.
        kind = node.get("type", "?")
        if kind != "VARREF":
            return Unsupported(kind, _line(node))
        var = self._resolve(node, "varp")
        owner = self.signals.get(var["addr"])
        if owner is None:
            # Declared in the parent, but not as a port or net: a parameter.
            return Unsupported(f"VARREF to {var.get('varType', '?')}", _line(node))
        if owner[0] != parent.name:
            raise VerilatorJSONError(f"{_describe(node)}: refers to a variable of module {owner[0]}")
        return NetRef(owner[1])


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

_ARROWS = {Direction.INPUT: "<-", Direction.OUTPUT: "->", Direction.INOUT: "<->"}


def _expr_text(expr: Expr) -> str:
    if isinstance(expr, NetRef):
        return expr.net
    where = f" (line {expr.line})" if expr.line is not None else ""
    return f"? {expr.kind}{where}"


def render_module_text(design: Design, module: Module) -> List[str]:
    lines = [f"module {module.name}"]
    for port in module.ports.values():
        lines.append(f"  {port.direction.value:<6} {port.display}")
    for net in module.nets.values():
        lines.append(f"  {'net':<6} {net.display}")
    for inst in module.instances.values():
        lines.append(f"  instance {inst.name} : {inst.module}")
        ports = list(design.modules[inst.module].ports.values())
        width = max((len(p.display) for p in ports), default=0)
        for port in ports:
            expr = inst.connections.get(port.name)
            if expr is None:
                target = "(unconnected)"
            else:
                target = f"{_ARROWS[port.direction]} {_expr_text(expr)}"
            lines.append(f"    {port.direction.value:<6} {port.display:<{width}} {target}")
    return lines


def render_text(design: Design, module: Optional[str] = None) -> str:
    """A deterministic structural dump, of one module or the whole hierarchy."""
    modules = [design.module(module)] if module is not None else design.hierarchy()
    blocks = ["\n".join(render_module_text(design, m)) for m in modules]
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# Block diagram renderer
#
# Draws one module on a character grid the way a physical design flow would:
# place the instances in columns that follow the data flow, then route every
# net through the free cells with a maze router. A wire may cross another one
# only at a right angle, drawn as "┼", and never joins it there; a net only
# branches with "┬ ┴ ├ ┤", so the drawing can be read back unambiguously.
# ---------------------------------------------------------------------------

N, E, S, W = 1, 2, 4, 8
_STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
_OPPOSITE = {N: S, S: N, E: W, W: E}
_GLYPHS = {
    N: "│", S: "│", N | S: "│",
    E: "─", W: "─", E | W: "─",
    E | S: "┌", S | W: "┐", N | E: "└", N | W: "┘",
    N | E | S: "├", N | S | W: "┤", E | S | W: "┬", N | E | W: "┴",
}
CROSSING = "┼"
_ATTEMPTS = 6

# What --optimize can favour. Each goal is one term of the cost the router
# minimizes, and naming it multiplies that term by _EMPHASIS; naming several
# mixes them.
GOALS = ("length", "bends", "crossings")
_EMPHASIS = 5

Cell = Tuple[int, int]
Stub = Tuple[int, int, int]  # x, y, and the direction from the stub to what it serves


class DiagramError(RTLScopeError):
    """The diagram does not fit the requested size."""


@dataclass(frozen=True)
class Costs:
    """The routing objective: what a wire cell, a bend and a crossing cost."""

    length: int = 1
    bends: int = 2
    crossings: int = 3

    @classmethod
    def favouring(cls, goals: Sequence[str]) -> "Costs":
        unknown = [g for g in goals if g not in GOALS]
        if unknown:
            raise RTLScopeError(f"unknown optimization goal {unknown[0]!r}; choose from {', '.join(GOALS)}")
        base = cls()
        return cls(*(getattr(base, g) * (_EMPHASIS if g in goals else 1) for g in GOALS))


@dataclass
class _Box:
    title: str
    subtitle: str
    left: List[Port]
    right: List[Port]
    slot: int
    # Ports drawn with a "?" stub, connected to something rtlscope cannot show.
    unsupported: List[str] = field(default_factory=list)
    connected: List[str] = field(default_factory=list)
    x: int = 0
    y: int = 0

    @property
    def inner(self) -> int:
        lw = max((len(p.display) for p in self.left), default=0)
        rw = max((len(p.display) for p in self.right), default=0)
        return max(len(self.title), len(self.subtitle), lw + rw + (2 if lw and rw else 0))

    @property
    def width(self) -> int:
        return self.inner + 2

    @property
    def title_rows(self) -> int:
        return 2 if self.subtitle else 1

    @property
    def height(self) -> int:
        return 2 + self.title_rows + max(len(self.left), len(self.right))

    def stub(self, port: Port) -> Stub:
        if port in self.left:
            return self.x - 1, self.y + 1 + self.title_rows + self.left.index(port), E
        return self.x + self.width, self.y + 1 + self.title_rows + self.right.index(port), W

    def draw(self, text: Dict[Cell, str]) -> None:
        inner = self.inner
        rows = ["┌" + "─" * inner + "┐", "│" + self.title.center(inner) + "│"]
        if self.subtitle:
            rows.append("│" + self.subtitle.center(inner) + "│")
        for i in range(max(len(self.left), len(self.right))):
            lport = self.left[i] if i < len(self.left) else None
            rport = self.right[i] if i < len(self.right) else None
            lname = lport.display if lport else ""
            rname = rport.display if rport else ""
            lchar = "┤" if lport and lport.name in self.connected else "│"
            rchar = "├" if rport and rport.name in self.connected else "│"
            rows.append(lchar + lname.ljust(inner - len(rname)) + rname + rchar)
        rows.append("└" + "─" * inner + "┘")
        for dy, row in enumerate(rows):
            _put(text, self.x, self.y + dy, row)
        for port in self.left + self.right:
            if port.name in self.unsupported:
                x, y, _ = self.stub(port)
                text[(x, y)] = "?"


@dataclass
class _Label:
    """A net name at the left or right edge of the diagram.

    Its row is not chosen in advance. Like an unconstrained I/O pin, it goes
    wherever the cheapest wire reaches that edge.
    """

    text: str
    left: bool
    pad: int  # spaces between the text and the wire
    edge: int = 0  # x of the stub, the label's cell nearest the diagram
    prefer: int = 0  # the row favoured among equally cheap ones
    y: int = 0

    @property
    def width(self) -> int:
        return len(self.text) + self.pad + 1

    def wire(self) -> range:
        """Cells between the text and the stub, filled with wire."""
        return range(len(self.text) + self.pad, self.edge) if self.left else range(0)

    def text_x(self) -> int:
        return 0 if self.left else self.edge + 1 + self.pad

    def stub(self, y: int) -> Stub:
        return self.edge, y, W if self.left else E


_End = Union[Tuple[_Box, Port], _Label]


@dataclass
class _Net:
    name: str
    ends: List[_End] = field(default_factory=list)


def _put(text: Dict[Cell, str], x: int, y: int, string: str) -> None:
    for i, char in enumerate(string):
        text[(x + i, y)] = char


def _pin_stub(end: Tuple[_Box, Port]) -> Stub:
    return end[0].stub(end[1])


def _columns(design: Design, module: Module) -> List[List[Instance]]:
    """Instances layered by their longest path from the module inputs."""
    drivers: Dict[str, List[str]] = {}
    for inst in module.instances.values():
        for port in design.modules[inst.module].ports.values():
            expr = inst.connections.get(port.name)
            if port.direction is Direction.OUTPUT and isinstance(expr, NetRef):
                drivers.setdefault(expr.net, []).append(inst.name)
    preds: Dict[str, List[str]] = {}
    for inst in module.instances.values():
        preds[inst.name] = []
        for port in design.modules[inst.module].ports.values():
            expr = inst.connections.get(port.name)
            if port.direction is Direction.INPUT and isinstance(expr, NetRef):
                preds[inst.name] += [d for d in drivers.get(expr.net, []) if d != inst.name]

    # Cut cycles where a forward walk from the inputs first closes them, so a
    # feedback wire runs right to left and everything else left to right.
    def external(name: str) -> bool:
        inst = module.instances[name]
        return any(
            p.direction is Direction.INPUT and isinstance(inst.connections.get(p.name), NetRef)
            and not drivers.get(inst.connections[p.name].net)
            for p in design.modules[inst.module].ports.values()
        )

    succs: Dict[str, List[str]] = {name: [] for name in preds}
    for name, sources in preds.items():
        for source in sources:
            succs[source].append(name)
    feedback = set()
    state: Dict[str, int] = {}  # 1 on the walk, 2 finished
    for root in sorted(module.instances, key=lambda n: (bool(preds[n]) and not external(n))):
        if root in state:
            continue
        stack = [(root, iter(succs[root]))]
        state[root] = 1
        while stack:
            name, rest = stack[-1]
            nxt = next(rest, None)
            if nxt is None:
                state[name] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                feedback.add((name, nxt))
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(succs[nxt])))

    depth: Dict[str, int] = {}
    for name in _topological(preds, feedback):
        levels = [depth[p] for p in preds[name] if (p, name) not in feedback]
        depth[name] = 1 + max(levels, default=-1)

    columns: List[List[Instance]] = [[] for _ in range(max(depth.values(), default=-1) + 1)]
    for inst in module.instances.values():
        columns[depth[inst.name]].append(inst)

    # Barycenter ordering: sit each instance near the ones that drive it.
    position: Dict[str, int] = {}
    for column in columns:
        def key(item: Tuple[int, Instance]) -> float:
            index, inst = item
            known = [position[p] for p in preds[inst.name] if p in position]
            return sum(known) / len(known) if known else index

        column[:] = [inst for _, inst in sorted(enumerate(column), key=key)]
        position.update((inst.name, i) for i, inst in enumerate(column))
    return columns


def _topological(preds: Dict[str, List[str]], feedback: set) -> List[str]:
    """Kahn's algorithm over the acyclic part, keeping declaration order on ties."""
    remaining = {n: sum((p, n) not in feedback for p in ps) for n, ps in preds.items()}
    succs: Dict[str, List[str]] = {n: [] for n in preds}
    for name, ps in preds.items():
        for p in ps:
            if (p, name) not in feedback:
                succs[p].append(name)
    order = []
    ready = [n for n in preds if remaining[n] == 0]
    while ready:
        name = ready.pop(0)
        order.append(name)
        for nxt in succs[name]:
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                ready.append(nxt)
    return order


def _plan(design: Design, module: Module) -> Tuple[List[List[_Box]], List[_Label], List[_Label], List[_Net]]:
    columns = [
        [
            _Box(
                inst.name,
                inst.module,
                [p for p in design.modules[inst.module].ports.values() if p.direction is Direction.INPUT],
                [p for p in design.modules[inst.module].ports.values() if p.direction is not Direction.INPUT],
                slot=c + 1,
            )
            for inst in insts
        ]
        for c, insts in enumerate(_columns(design, module))
    ]
    nets = {name: _Net(name) for name in [*module.ports, *module.nets]}
    for column in columns:
        for box in column:
            inst = module.instances[box.title]
            for port in box.left + box.right:
                expr = inst.connections.get(port.name)
                if isinstance(expr, NetRef):
                    nets[expr.net].ends.append((box, port))
                    box.connected.append(port.name)
                elif isinstance(expr, Unsupported):
                    box.unsupported.append(port.name)
                    box.connected.append(port.name)

    lefts: List[_Label] = []
    rights: List[_Label] = []
    for net in nets.values():
        port = module.ports.get(net.name)
        directions = {p.direction for _, p in net.ends}
        if port is not None:
            source = port.direction is Direction.INPUT
            sink = not source
            marker = {Direction.INPUT: "▶", Direction.OUTPUT: "▶", Direction.INOUT: "◆"}[port.direction]
            text, pad = (f"{port.display} {marker}", 0) if source else (f"{marker} {port.display}", 0)
        elif not net.ends:
            continue
        else:
            # Driven or read by logic rtlscope does not draw: show where it leaves.
            source = not directions & {Direction.OUTPUT, Direction.INOUT}
            sink = not directions & {Direction.INPUT, Direction.INOUT}
            text, pad = module.nets[net.name].display, 1
        if source:
            label = _Label(text, True, pad)
            lefts.append(label)
            net.ends.insert(0, label)
        if sink:
            label = _Label(text, False, pad)
            rights.append(label)
            net.ends.append(label)
    return columns, lefts, rights, list(nets.values())


def _slot_of(end: _End, last: int) -> int:
    if isinstance(end, _Label):
        return 0 if end.left else last
    return end[0].slot


def _place(columns, lefts, rights, nets, extra: int, vgap: int, max_width: int) -> Tuple[int, int, int, int]:
    """Assign coordinates; return the grid size and the routable columns."""
    last = len(columns) + 1
    widths = [max((l.width for l in lefts), default=0)]
    widths += [max(b.width for b in column) for column in columns]
    widths.append(max((l.width for l in rights), default=0))
    # A channel needs a track for each net crossing it, and room for the stub
    # and access cell of the pins on both sides.
    tracks = [0] * last
    for net in nets:
        slots = [_slot_of(end, last) for end in net.ends]
        for gap in range(min(slots, default=0), max(slots, default=0)):
            tracks[gap] += 1
    base = sum(widths) + sum(t + 4 for t in tracks)
    if base > max_width:
        raise DiagramError(f"diagram needs at least {base} columns, but the width is {max_width}")
    extra = min(extra, (max_width - base) // last)
    gaps = [t + 4 + extra for t in tracks]

    xs = [0]
    for width, gap in zip(widths, gaps):
        xs.append(xs[-1] + width + gap)
    heights = [sum(b.height for b in column) + vgap * (len(column) - 1) for column in columns]
    body = max([*heights, len(lefts), len(rights), 1])
    # One free row beyond the keep-out ring, so wires can pass over the boxes.
    margin = vgap + 1
    height = body + 2 * margin
    for column, column_height in zip(columns, heights):
        y = margin + (body - column_height) // 2
        for box in column:
            box.x = xs[box.slot] + (widths[box.slot] - box.width) // 2
            box.y = y
            y += box.height + vgap

    for labels, edge in ((lefts, widths[0] - 1), (rights, xs[last])):
        for label in labels:
            net = next(n for n in nets if label in n.ends)
            rows = [_pin_stub(end)[1] for end in net.ends if not isinstance(end, _Label)]
            label.edge = edge
            label.prefer = rows[0] if rows else margin
    return xs[last] + widths[-1], height, widths[0], xs[last] - 1


class _Router:
    def __init__(self, width: int, height: int, text: Dict[Cell, str], xmin: int, xmax: int,
                 costs: Costs = Costs()) -> None:
        self.width = width
        self.costs = costs
        self.height = height
        self.text = text
        # Wires stay between the label columns; labels are reached through their stubs.
        self.xmin = xmin
        self.xmax = xmax
        self.wires: Dict[Cell, Dict[str, int]] = {}
        # Pin and label stubs are never crossed: "┼┤" would read as a connection.
        self.stubs: set = set()
        # A ring of free cells around every box, so no wire runs along a border.
        self.keepout: set = set()
        self.label_rows: Dict[bool, set] = {True: set(), False: set()}

    def _add(self, cell: Cell, net: str, bits: int) -> None:
        masks = self.wires.setdefault(cell, {})
        masks[net] = masks.get(net, 0) | bits

    def keep_clear(self, box: _Box) -> None:
        x0, y0, x1, y1 = box.x - 1, box.y - 1, box.x + box.width, box.y + box.height
        for x in range(x0, x1 + 1):
            self.keepout.update({(x, y0), (x, y1)})
        for y in range(y0, y1 + 1):
            self.keepout.update({(x0, y), (x1, y)})

    def claim(self, net: str, stub: Stub) -> Stub:
        """Reserve a stub and the access cell beyond it; return where routing starts.

        The access cell keeps every pin reachable: no other net can take the
        one cell a pin has to leave through.
        """
        x, y, bit = stub
        dx, dy = _STEP[_OPPOSITE[bit]]
        access = (x + dx, y + dy)
        for cell in ((x, y), access):
            if cell in self.text or cell in self.wires or not (0 <= cell[0] < self.width and 0 <= cell[1] < self.height):
                raise RuntimeError(f"stub of {net} at {cell} overlaps the diagram")
            self.stubs.add(cell)
        self._add((x, y), net, bit | _OPPOSITE[bit])
        self._add(access, net, bit)
        return access[0], access[1], bit

    def route(self, net: str, stubs: List[Stub], labels: List[_Label]) -> bool:
        """Connect the pins as one tree, nearest first, then bring in the labels."""
        tree: set = set()
        if stubs:
            first = stubs[0]
            tree.add(first[:2])
            rest = sorted(stubs[1:], key=lambda s: abs(s[0] - first[0]) + abs(s[1] - first[1]))
            pending = {s[:2] for s in rest}
            for x, y, bit in rest:
                pending.discard((x, y))
                path = self._search(net, {(x, y): bit}, tree, pending)
                if path is None:
                    return False
                self._commit(net, path, tree)
        for label in labels:
            if not self._route_label(net, label, tree):
                return False
        return True

    def _commit(self, net: str, path, tree: set) -> None:
        for (cell, step), nxt in zip(path, path[1:]):
            self._add(cell, net, step)
            self._add(nxt[0], net, _OPPOSITE[step])
        tree.update(cell for cell, _ in path)

    def _label_cells(self, label: _Label, row: int) -> List[Cell]:
        x, y, bit = label.stub(row)
        dx, _ = _STEP[_OPPOSITE[bit]]
        text = [(label.text_x() + i, row) for i in range(len(label.text))]
        return text + [(x, row) for x in label.wire()] + [(x, y), (x + dx, y)]

    def _route_label(self, net: str, label: _Label, tree: set) -> bool:
        """Route to whichever free row of the label's edge is cheapest, and put it there."""
        rows = [
            row
            for row in range(self.height)
            if row not in self.label_rows[label.left]
            and not any(c in self.text or c in self.wires for c in self._label_cells(label, row))
        ]
        rows.sort(key=lambda row: (abs(row - label.prefer), row))
        if not rows:
            return False
        if not tree:
            # Nothing inside the module is on this net: the label stands alone.
            tree.add(self._claim_label(net, label, rows[0]))
            return True
        starts = {}
        for row in rows:
            x, y, bit = label.stub(row)
            dx, _ = _STEP[_OPPOSITE[bit]]
            starts[(x + dx, y)] = bit
        path = self._search(net, starts, tree, set())
        if path is None:
            return False
        self._claim_label(net, label, path[0][0][1])
        self._commit(net, path, tree)
        return True

    def _claim_label(self, net: str, label: _Label, row: int) -> Cell:
        label.y = row
        self.label_rows[label.left].add(row)
        _put(self.text, label.text_x(), row, label.text)
        for x in label.wire():
            self._add((x, row), net, E | W)
            self.stubs.add((x, row))
        x, y, _ = self.claim(net, label.stub(row))
        return x, y

    def _search(self, net: str, starts: Dict[Cell, int], tree: set, pending: set):
        """Dijkstra over (cell, heading) from any start; returns [(cell, step to next)].

        ``starts`` maps each start cell to the direction of its stub, which the
        path may not leave through. The path ends on a cell of ``tree``.
        """
        frontier = [(0, counter, start, 0) for counter, start in enumerate(starts)]
        counter = len(frontier)
        best: Dict[Tuple[Cell, int], int] = {(start, 0): 0 for start in starts}
        parent: Dict[Tuple[Cell, int], Optional[Tuple[Cell, int]]] = {(start, 0): None for start in starts}
        done = set()
        while frontier:
            cost, _, cell, heading = heapq.heappop(frontier)
            state = (cell, heading)
            if state in done:
                continue
            done.add(state)
            if cell in tree:
                return self._path(parent, state)
            crossing = heading != 0 and bool(self.wires.get(cell))
            for step in (E, S, W, N):
                if heading and step == _OPPOSITE[heading]:
                    continue
                if heading == 0 and step == starts[cell]:
                    continue
                if crossing and step != heading:
                    continue
                dx, dy = _STEP[step]
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (self.xmin <= nxt[0] <= self.xmax and 0 <= nxt[1] < self.height):
                    continue
                if nxt in self.text or nxt in pending:
                    continue
                if nxt in self.keepout and nxt not in self.stubs:
                    continue
                masks = self.wires.get(nxt, {})
                step_cost = self.costs.length + (self.costs.bends if heading and step != heading else 0)
                if nxt in tree:
                    back = _OPPOSITE[step]
                    mask = masks[net]
                    # Join only where it stays a plain branch, never a 4-way.
                    if len(masks) > 1 or mask & back or bin(mask | back).count("1") > 3:
                        continue
                elif nxt in self.stubs:
                    continue
                elif masks:
                    other = next(iter(masks.values())) if len(masks) == 1 else None
                    if other != (N | S if step in (E, W) else E | W):
                        continue
                    step_cost += self.costs.crossings
                key = (nxt, step)
                if key in done or best.get(key, cost + step_cost + 1) <= cost + step_cost:
                    continue
                best[key] = cost + step_cost
                parent[key] = state
                counter += 1
                heapq.heappush(frontier, (cost + step_cost, counter, nxt, step))
        return None

    @staticmethod
    def _path(parent, state):
        states = []
        while state is not None:
            states.append(state)
            state = parent[state]
        states.reverse()
        # Pair each cell with the step leaving it towards the next one.
        return [(cell, states[i + 1][1] if i + 1 < len(states) else 0) for i, (cell, _) in enumerate(states)]

    def score(self) -> int:
        """The objective over the finished drawing, which is what orders compete on."""
        length = bends = crossings = 0
        for masks in self.wires.values():
            crossings += len(masks) > 1
            for mask in masks.values():
                length += 1
                bends += mask in (E | S, S | W, N | E, N | W)
        return length * self.costs.length + bends * self.costs.bends + crossings * self.costs.crossings

    def render(self) -> List[str]:
        lines = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                if (x, y) in self.text:
                    row.append(self.text[(x, y)])
                elif (x, y) in self.wires:
                    masks = self.wires[(x, y)]
                    row.append(CROSSING if len(masks) > 1 else _GLYPHS[next(iter(masks.values()))])
                else:
                    row.append(" ")
            lines.append("".join(row).rstrip())
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        indent = min((len(l) - len(l.lstrip(" ")) for l in lines if l), default=0)
        return [l[indent:] for l in lines]


def _orders(nets: List[_Net]) -> List[List[_Net]]:
    """Net orders worth trying: routing is sequential, so order shapes the result."""

    def span(net: _Net) -> int:
        stubs = [_pin_stub(e) for e in net.ends if not isinstance(e, _Label)]
        if not stubs:
            return 0
        xs, ys = [s[0] for s in stubs], [s[1] for s in stubs]
        return max(xs) - min(xs) + max(ys) - min(ys)

    orders = [
        list(nets),
        list(reversed(nets)),
        sorted(nets, key=lambda n: -len(n.ends)),
        sorted(nets, key=span),
        sorted(nets, key=lambda n: -span(n)),
    ]
    unique = []
    for order in orders:
        if order not in unique:
            unique.append(order)
    return unique


def _route_in_order(columns, order: List[_Net], size, text: Dict[Cell, str], costs: Costs) -> Optional[_Router]:
    """Route every net starting from one order, or None when none tried works.

    Rip-up and reroute: when a net cannot be routed, the nets routed before it
    are what boxed it in, so everything is torn up and it goes first.
    """
    order = list(order)
    tried = set()
    while tuple(n.name for n in order) not in tried:
        tried.add(tuple(n.name for n in order))
        router = _Router(size[0], size[1], dict(text), size[2], size[3], costs)
        for box in (b for column in columns for b in column):
            router.keep_clear(box)
        starts = {
            net.name: [router.claim(net.name, _pin_stub(e)) for e in net.ends if not isinstance(e, _Label)]
            for net in order
        }
        failed = next(
            (
                n
                for n in order
                if not router.route(n.name, starts[n.name], [e for e in n.ends if isinstance(e, _Label)])
            ),
            None,
        )
        if failed is None:
            return router
        order.remove(failed)
        order.insert(0, failed)
    return None


def _route(columns, nets: List[_Net], size, text: Dict[Cell, str], costs: Costs) -> Optional[_Router]:
    """The best-scoring routing over several net orders; the first wins ties."""
    best: Optional[_Router] = None
    for order in _orders(nets):
        router = _route_in_order(columns, order, size, text, costs)
        if router is not None and (best is None or router.score() < best.score()):
            best = router
    return best


def render_diagram(
    design: Design, module: Module, width: int, height: Optional[int] = None, costs: Costs = Costs()
) -> str:
    """One module as a box-and-wire block diagram at most ``width`` columns wide.

    ``costs`` is the objective the routing minimizes. Raises DiagramError when
    the module does not fit, or does not fit in ``height`` rows when given.
    """
    if not module.instances:
        box = _Box(module.name, "", [p for p in module.ports.values() if p.direction is Direction.INPUT],
                   [p for p in module.ports.values() if p.direction is not Direction.INPUT], slot=0)
        text: Dict[Cell, str] = {}
        box.draw(text)
        lines = _Router(box.width, box.height, text, 0, box.width - 1).render()
        return _check_size(lines, module, width, height)

    for attempt in range(_ATTEMPTS):
        columns, lefts, rights, nets = _plan(design, module)
        size = _place(columns, lefts, rights, nets, attempt, 1 + 2 * attempt, width)
        text = {}
        for box in (b for column in columns for b in column):
            box.draw(text)
        router = _route(columns, nets, size, text, costs)
        if router is not None:
            return _check_size(router.render(), module, width, height)
    raise DiagramError(f"cannot route module {module.name} within a width of {width}")


def _check_size(lines: List[str], module: Module, width: int, height: Optional[int]) -> str:
    needed = max((len(l) for l in lines), default=0)
    if needed > width:
        raise DiagramError(f"diagram needs at least {needed} columns, but the width is {width}")
    if height is not None and len(lines) > height:
        raise DiagramError(f"diagram of {module.name} needs {len(lines)} rows, but the height is {height}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtlscope",
        description="Show the module hierarchy and connectivity of an elaborated RTL design.",
    )
    parser.add_argument("file", type=Path, help="Verilator JSON tree (from --json-only)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="show tracebacks for errors")
    parser.add_argument(
        "--format",
        choices=("diagram", "text"),
        default="diagram",
        help="diagram: block diagram of one module (default); text: structural dump",
    )
    parser.add_argument(
        "--module",
        metavar="NAME",
        help="show only this module (default: the top module for diagram, the whole hierarchy for text)",
    )
    parser.add_argument(
        "--width",
        type=_positive,
        metavar="COLUMNS",
        help="maximum diagram width (default: the terminal width)",
    )
    parser.add_argument(
        "--height",
        type=_positive,
        metavar="ROWS",
        help="maximum diagram height; a diagram that needs more is an error",
    )
    parser.add_argument(
        "--optimize",
        metavar="GOALS",
        type=lambda text: [g for g in text.split(",") if g],
        default=[],
        help=f"favour some of {', '.join(GOALS)}; separate several with commas to mix them",
    )
    return parser


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    return value


def run(args: argparse.Namespace) -> int:
    design = load_verilator_json(args.file)
    if args.format == "text":
        print(render_text(design, args.module), end="")
        return 0
    module = design.module(args.module) if args.module is not None else design.top
    width = args.width if args.width is not None else shutil.get_terminal_size().columns
    costs = Costs.favouring(args.optimize)
    print(render_diagram(design, module, width, args.height, costs), end="")
    return 0


def _discard_stdout() -> None:
    """Point stdout at /dev/null so interpreter shutdown will not flush a dead pipe."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # The reader went away, as in "rtlscope design.tree.json | head".
        _discard_stdout()
        return 141
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        elif isinstance(exc, RTLScopeError):
            print(f"rtlscope: error: {exc}", file=sys.stderr)
        else:
            print(f"rtlscope: error: unexpected failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
