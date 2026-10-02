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
from dataclasses import dataclass, field, replace
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


def _signal_text(name: str, array: Tuple[Bits, ...], bits: Optional[Bits]) -> str:
    """``mem[0:3][7:0]``: dimensions in the order an expression indexes them."""
    return name + "".join(map(str, array)) + (str(bits) if bits else "")


@dataclass(frozen=True)
class Port:
    name: str
    direction: Direction
    bits: Optional[Bits] = None  # None for a single bit
    clock: bool = False  # an edge that clocks the module, or its children
    array: Tuple[Bits, ...] = ()  # unpacked dimensions, outermost first

    @property
    def display(self) -> str:
        return _signal_text(self.name, self.array, self.bits)


@dataclass(frozen=True)
class Net:
    """A module-level signal that is not a port."""

    name: str
    bits: Optional[Bits] = None  # None for a single bit
    array: Tuple[Bits, ...] = ()  # unpacked dimensions, outermost first

    @property
    def display(self) -> str:
        return _signal_text(self.name, self.array, self.bits)


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


@dataclass(frozen=True)
class Process:
    """A process of a module, seen only from outside: what it reads and writes.

    Its contents are not modelled. ``kind`` is how it was written:
    ``always_ff``, ``always_comb``, ``always_latch``, ``always``, ``assign``,
    ``initial`` or ``final``. Signals are module-level ports and nets, in
    declaration order; ``clocks`` are not repeated in ``reads``.
    """

    kind: str
    line: Optional[int]
    reads: Tuple[str, ...] = ()
    writes: Tuple[str, ...] = ()
    clocks: Tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """``always_ff, line 9``: how the source would point at it."""
        return f"{self.kind}, line {self.line}" if self.line is not None else self.kind


@dataclass
class Module:
    # Unique within the design. A parameterized module elaborated with other
    # values has a name of its own, and keeps the source name in ``source``.
    name: str
    ports: Dict[str, Port] = field(default_factory=dict)
    # Module-level signals that are not ports, in declaration order.
    nets: Dict[str, Net] = field(default_factory=dict)
    instances: Dict[str, Instance] = field(default_factory=dict)
    processes: List[Process] = field(default_factory=list)
    source: str = ""  # the name written in the source; empty means ``name``
    # Overridable parameters and their elaborated values, in declaration order.
    params: Dict[str, str] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        """``bit_reverse #(WIDTH=4)``: the source name and parameter values."""
        base = self.source or self.name
        if not self.params:
            return base
        return base + " #(" + ", ".join(f"{k}={v}" for k, v in self.params.items()) + ")"


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
_PROCESS_KINDS = {
    "always_ff": "always_ff",
    "always_comb": "always_comb",
    "always_latch": "always_latch",
    "always": "always",
    "cont_assign": "assign",
}
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


def _const_text(name: str) -> str:
    """A Verilator constant such as ``32'sh4`` as a number people write: ``4``."""
    width, quote, rest = name.partition("'")
    if not quote or not rest:
        return name
    signed = rest[0] == "s"
    rest = rest[1:] if signed else rest
    base = {"h": 16, "d": 10, "b": 2, "o": 8}.get(rest[:1])
    try:
        value = int(rest[1:], base) if base else None
        bits = int(width)
    except ValueError:
        return name
    if value is None:
        return name
    if signed and bits and value >= 1 << (bits - 1):
        value -= 1 << bits
    return str(value)


def _param_value(var: Dict[str, Any]) -> str:
    values = var.get("valuep") or []
    if len(values) == 1 and values[0].get("type") == "CONST":
        return _const_text(values[0].get("name", "?"))
    return "?"


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
            modules[node["name"]].processes = self._read_processes(node, modules[node["name"]])
        self._mark_clocks(modules)
        return Design(modules)

    def _read_processes(self, node: Dict[str, Any], module: Module) -> List[Process]:
        """Every process of a module, by the module signals it reads and writes.

        A clock is an edge in the sensitivity list that the body never reads.
        That is how synthesis tells it from an asynchronous reset: in
        ``always_ff @(posedge clk or negedge rst_n)`` both are edges, but the
        body tests ``rst_n`` and never ``clk``.
        """
        order = {name: i for i, name in enumerate([*module.ports, *module.nets])}
        processes = []
        for stmt in node.get("stmtsp", []):
            kind = stmt.get("type")
            if kind == "ALWAYS":
                kind = _PROCESS_KINDS.get(stmt.get("keyword"), "always")
            elif kind in ("INITIAL", "FINAL"):
                kind = kind.lower()
            else:
                continue

            def signals(nodes: Any, accesses: Tuple[str, ...]) -> set:
                found = set()
                for ref in _walk(nodes):
                    if ref.get("type") == "VARREF" and ref.get("access") in accesses:
                        owner = self.signals.get(ref.get("varp"))
                        # Block-local variables, like a loop index, are not module signals.
                        if owner is not None and owner[0] == module.name:
                            found.add(owner[1])
                return found

            body = stmt.get("stmtsp", [])
            edges = {
                name
                for item in _walk(stmt.get("sentreep", []))
                if item.get("type") == "SENITEM" and item.get("edgeType") in ("POS", "NEG", "BOTH")
                for name in signals(item.get("sensp", []), ("RD", "RW"))
            }
            clocks = edges - signals(body, ("RD", "RW"))
            reads = signals(stmt.get("sentreep", []), ("RD", "RW")) | signals(body, ("RD", "RW"))

            def ordered(names: set) -> Tuple[str, ...]:
                return tuple(sorted(names, key=order.__getitem__))

            processes.append(
                Process(kind, _line(stmt), ordered(reads - clocks), ordered(signals(body, ("WR", "RW"))), ordered(clocks))
            )
        return processes

    @staticmethod
    def _mark_clocks(modules: Dict[str, Module]) -> None:
        """Mark the ports that clock a process, or a child's clock port."""

        def mark(module: Module, name: str) -> bool:
            port = module.ports.get(name)
            if port is None or port.clock:
                return False
            module.ports[name] = replace(port, clock=True)
            return True

        for module in modules.values():
            for process in module.processes:
                for name in process.clocks:
                    mark(module, name)
        changed = True
        while changed:
            changed = False
            for module in modules.values():
                for inst in module.instances.values():
                    for port in modules[inst.module].ports.values():
                        expr = inst.connections.get(port.name)
                        if port.clock and isinstance(expr, NetRef):
                            changed |= mark(module, expr.net)

    def _resolve(self, node: Dict[str, Any], key: str) -> Dict[str, Any]:
        addr = node.get(key)
        target = self.nodes.get(addr) if addr not in (None, _UNLINKED) else None
        if target is None:
            raise VerilatorJSONError(f"{_describe(node)}: unresolved {key} ({addr})")
        return target

    def _shape(self, var: Dict[str, Any]) -> Tuple[Tuple[Bits, ...], Optional[Bits]]:
        """A variable's unpacked dimensions and packed range, through typedefs."""
        array: List[Bits] = []
        dtype = self._resolve(var, "dtypep")
        for _ in range(64):
            kind = dtype.get("type")
            if kind == "BASICDTYPE":
                return tuple(array), self._range(var, dtype["range"]) if "range" in dtype else None
            if kind == "UNPACKARRAYDTYPE":
                array.append(self._range(var, dtype.get("declRange", "").strip("[]")))
            # A typedef and an enum are as wide as the type they refer to.
            elif kind not in ("REFDTYPE", "ENUMDTYPE"):
                break
            dtype = self._resolve(dtype, "refDTypep")
        kind = {"PACKARRAYDTYPE": "multi-dimensional packed arrays"}.get(dtype.get("type"), f"data type {dtype.get('type')}")
        raise VerilatorJSONError(f"{_describe(var)}: {kind} not supported yet")

    @staticmethod
    def _range(var: Dict[str, Any], text: str) -> Bits:
        msb, _, lsb = text.partition(":")
        try:
            return Bits(int(msb), int(lsb))
        except ValueError:
            raise VerilatorJSONError(f"{_describe(var)}: unreadable range {text!r}") from None

    def _read_signals(self, node: Dict[str, Any]) -> Module:
        module = Module(node["name"], source=node.get("origName", node["name"]))
        for stmt in node.get("stmtsp", []):
            if stmt.get("type") != "VAR":
                continue
            name = stmt["name"]
            if stmt.get("varType") == "GPARAM":
                module.params[name] = _param_value(stmt)
            direction = stmt.get("direction", "NONE")
            if direction in _DIRECTIONS:
                array, bits = self._shape(stmt)
                port = Port(name, _DIRECTIONS[direction], bits, array=array)
                module.ports[name] = port
                self.ports[stmt["addr"]] = port
            elif direction != "NONE":
                raise VerilatorJSONError(f"{_describe(stmt)}: unsupported port direction {direction}")
            elif stmt.get("varType") in _NON_NET_VAR_TYPES:
                continue
            else:
                array, bits = self._shape(stmt)
                module.nets[name] = Net(name, bits, array)
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
    header = f"module {module.name}"
    if module.signature != module.name:
        header += f"  ({module.signature})"
    lines = [header]
    for port in module.ports.values():
        lines.append(f"  {port.direction.value:<6} {port.display}" + (" (clock)" if port.clock else ""))
    for net in module.nets.values():
        lines.append(f"  {'net':<6} {net.display}")
    for process in module.processes:
        lines.append(f"  process {process.name}")
        for label, names in (("clock", process.clocks), ("reads", process.reads), ("writes", process.writes)):
            if names:
                lines.append(f"    {label:<6} {', '.join(names)}")
    for inst in module.instances.values():
        lines.append(f"  instance {inst.name} : {design.modules[inst.module].signature}")
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
GOALS = ("length", "bends", "crossings", "spacing")
_EMPHASIS = 5

Cell = Tuple[int, int]
Stub = Tuple[int, int, int]  # x, y, and the direction from the stub to what it serves


class DiagramError(RTLScopeError):
    """The diagram does not fit the requested size."""


@dataclass(frozen=True)
class Costs:
    """The routing objective: what a wire cell, a bend, a crossing and a
    vertical wire right beside another net's vertical wire cost."""

    length: int = 1
    bends: int = 2
    crossings: int = 3
    spacing: int = 1

    @classmethod
    def favouring(cls, goals: Sequence[str]) -> "Costs":
        unknown = [g for g in goals if g not in GOALS]
        if unknown:
            raise RTLScopeError(f"unknown optimization goal {unknown[0]!r}; choose from {', '.join(GOALS)}")
        base = cls()
        return cls(*(getattr(base, g) * (_EMPHASIS if g in goals else 1) for g in GOALS))


def _pin_text(port: Port) -> str:
    """A port as written inside a box; "▷" is the flip-flop symbol's clock."""
    return ("▷" if port.clock else "") + port.display


@dataclass
class _Box:
    name: str  # the instance, or the module for a lone box
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
        lw = max((len(_pin_text(p)) for p in self.left), default=0)
        rw = max((len(_pin_text(p)) for p in self.right), default=0)
        return max(len(self.title) + 2, len(self.subtitle) + 2, lw + rw + (2 if lw and rw else 0))

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
            lname = _pin_text(lport) if lport else ""
            rname = _pin_text(rport) if rport else ""
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


@dataclass
class _Cell:
    """Something drawn as a box: an instance, or a process of the module."""

    name: str
    title: str
    subtitle: str
    ports: List[Port]
    connections: Dict[str, Expr]


def _cells(design: Design, module: Module) -> List[_Cell]:
    cells = []
    for inst in module.instances.values():
        child = design.modules[inst.module]
        title = f"{inst.name} : {child.source or child.name}"
        cells.append(_Cell(inst.name, title, _params_text(child), list(child.ports.values()), inst.connections))
    signals = {**module.nets, **module.ports}
    taken = set(module.instances)
    for process in module.processes:
        name = process.name
        while name in taken:
            name += "'"
        taken.add(name)
        # A process box's pins are named after the signals they carry.
        ports = [replace(_as_port(signals[n], Direction.INPUT), clock=True) for n in process.clocks]
        ports += [_as_port(signals[n], Direction.INPUT) for n in process.reads]
        ports += [_as_port(signals[n], Direction.OUTPUT) for n in process.writes]
        connections = {p.name: NetRef(p.name) for p in ports}
        cells.append(_Cell(name, name, "", ports, connections))
    return cells


def _as_port(signal: Union[Port, Net], direction: Direction) -> Port:
    return Port(signal.name, direction, signal.bits, array=signal.array)


def _columns(cells: List[_Cell]) -> List[List[_Cell]]:
    """Cells layered by their longest path from the module inputs."""
    by_name = {cell.name: cell for cell in cells}

    def nets(cell: _Cell, direction: Direction) -> List[str]:
        refs = [cell.connections.get(p.name) for p in cell.ports if p.direction is direction]
        return [r.net for r in refs if isinstance(r, NetRef)]

    drivers: Dict[str, List[str]] = {}
    for cell in cells:
        for net in nets(cell, Direction.OUTPUT):
            drivers.setdefault(net, []).append(cell.name)
    preds: Dict[str, List[str]] = {}
    for cell in cells:
        preds[cell.name] = []
        for net in nets(cell, Direction.INPUT):
            preds[cell.name] += [d for d in drivers.get(net, []) if d != cell.name]

    # Cut cycles where a forward walk from the inputs first closes them, so a
    # feedback wire runs right to left and everything else left to right.
    def external(name: str) -> bool:
        return any(not drivers.get(net) for net in nets(by_name[name], Direction.INPUT))

    succs: Dict[str, List[str]] = {name: [] for name in preds}
    for name, sources in preds.items():
        for source in sources:
            succs[source].append(name)
    feedback = set()
    state: Dict[str, int] = {}  # 1 on the walk, 2 finished
    for root in sorted(by_name, key=lambda n: (bool(preds[n]) and not external(n))):
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

    columns: List[List[_Cell]] = [[] for _ in range(max(depth.values(), default=-1) + 1)]
    for cell in cells:
        columns[depth[cell.name]].append(cell)

    # Barycenter ordering: sit each cell near the ones that drive it.
    position: Dict[str, int] = {}
    for column in columns:
        def key(item: Tuple[int, _Cell]) -> float:
            index, cell = item
            known = [position[p] for p in preds[cell.name] if p in position]
            return sum(known) / len(known) if known else index

        column[:] = [cell for _, cell in sorted(enumerate(column), key=key)]
        position.update((cell.name, i) for i, cell in enumerate(column))
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


def _params_text(module: Module) -> str:
    return "#(" + ", ".join(f"{k}={v}" for k, v in module.params.items()) + ")" if module.params else ""


def _plan(design: Design, module: Module) -> Tuple[List[List[_Box]], List[_Label], List[_Label], List[_Net]]:
    cells = _columns(_cells(design, module))
    columns = [
        [
            _Box(
                cell.name,
                cell.title,
                cell.subtitle,
                [p for p in cell.ports if p.direction is Direction.INPUT],
                [p for p in cell.ports if p.direction is not Direction.INPUT],
                slot=c + 1,
            )
            for cell in column
        ]
        for c, column in enumerate(cells)
    ]
    connections = {cell.name: cell.connections for column in cells for cell in column}
    nets = {name: _Net(name) for name in [*module.ports, *module.nets]}
    for column in columns:
        for box in column:
            for port in box.left + box.right:
                expr = connections[box.name].get(port.name)
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


def _channel_of(end: _End, slot: int) -> int:
    """The channel an end opens onto: right of its slot, or left of it."""
    if isinstance(end, _Label):
        return slot if end.left else slot - 1
    return slot - 1 if end[1] in end[0].left else slot


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
    # A net needs a track in a channel it crosses, or in one that two of its
    # ends open onto, such as two input pins of the same column.
    tracks = [0] * last
    for net in nets:
        if not net.ends:
            continue
        slots = [_slot_of(end, last) for end in net.ends]
        opening = [0] * last
        for end, slot in zip(net.ends, slots):
            opening[_channel_of(end, slot)] += 1
        for gap in range(last):
            if min(slots) <= gap < max(slots) or opening[gap] > 1:
                tracks[gap] += 1
    # Each side of a channel with pins holds their stubs and access cells; an
    # outer channel has pins on one side only unless that edge has labels.
    sides = [4] * last
    sides[0] = 2 + (2 if lefts else 0)
    sides[-1] = 2 + (2 if rights else 0)
    base = sum(widths) + sum(t + side for t, side in zip(tracks, sides))
    if base > max_width:
        raise DiagramError(f"diagram needs at least {base} columns, but the width is {max_width}")
    # With room to spare, a free column between tracks keeps parallel wires apart.
    spaced = [2 * t - 1 + side if t else side for t, side in zip(tracks, sides)]
    if sum(widths) + sum(spaced) <= max_width:
        channels = spaced
    else:
        channels = [t + side for t, side in zip(tracks, sides)]
    extra = min(extra, (max_width - sum(widths) - sum(channels)) // last)
    gaps = [c + extra for c in channels]

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
                step_cost += self.costs.spacing * self._neighbours(net, nxt, step)
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

    def _neighbours(self, net: str, cell: Cell, step: int) -> int:
        """Other nets' vertical wires in the cells beside a vertical step.

        Only vertical runs count: horizontal wires into consecutive pins of a
        box are one row apart by construction, while vertical tracks in a
        channel can be spread whenever there is room.
        """
        if step in (E, W):
            return 0
        count = 0
        for dx in (-1, 1):
            for other, mask in self.wires.get((cell[0] + dx, cell[1]), {}).items():
                count += other != net and bool(mask & (N | S))
        return count

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
        length = bends = crossings = crowding = 0
        for (x, y), masks in self.wires.items():
            crossings += len(masks) > 1
            for net, mask in masks.items():
                length += 1
                bends += mask in (E | S, S | W, N | E, N | W)
                # Vertical wires side by side, each pair counted once.
                if mask & (N | S):
                    right = self.wires.get((x + 1, y), {})
                    crowding += sum(other != net and bool(m & (N | S)) for other, m in right.items())
        return (
            length * self.costs.length
            + bends * self.costs.bends
            + crossings * self.costs.crossings
            + crowding * self.costs.spacing
        )

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
    if not module.instances and not module.processes:
        box = _Box(module.name, module.source or module.name, _params_text(module), [p for p in module.ports.values() if p.direction is Direction.INPUT],
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
