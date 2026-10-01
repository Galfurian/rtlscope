"""Builders for small Verilator JSON trees, and access to the real fixture.

The builders produce only the fields the frontend reads, in the same shape
Verilator writes them. Nodes nobody points at get no ``addr``.
"""

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMBINATIONAL = FIXTURES / "Vtb_combinational.tree.json"


def load_combinational():
    """A fresh decoded copy of the real fixture, safe to mutate."""
    return json.loads(COMBINATIONAL.read_text(encoding="utf-8"))


def loc(line):
    return f"e,{line}:1,{line}:2"


def var(addr, name, direction="NONE", var_type=None, line=1):
    if var_type is None:
        var_type = "VAR" if direction == "NONE" else "PORT"
    return {
        "type": "VAR",
        "name": name,
        "addr": addr,
        "loc": loc(line),
        "direction": direction,
        "varType": var_type,
    }


def varref(varp, name="ref", line=1):
    return {"type": "VARREF", "name": name, "loc": loc(line), "access": "RD", "varp": varp}


def pin(name, mod_varp, *exprs, line=1):
    return {"type": "PIN", "name": name, "loc": loc(line), "modVarp": mod_varp, "exprp": list(exprs)}


def cell(name, modp, *pins, mod_name="unused", line=1):
    return {
        "type": "CELL",
        "name": name,
        "loc": loc(line),
        "modName": mod_name,
        "modp": modp,
        "pinsp": list(pins),
        "paramsp": [],
        "rangep": [],
        "intfRefsp": [],
    }


def module(addr, name, *stmts, kind="MODULE"):
    return {"type": kind, "name": name, "addr": addr, "loc": loc(1), "stmtsp": list(stmts)}


def netlist(*modules):
    return {"type": "NETLIST", "name": "$root", "addr": "(B)", "modulesp": list(modules)}


def find_node(tree, kind, name):
    """The first node of a type and name, searched depth-first."""
    stack = [tree]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("type") == kind and item.get("name") == name:
                return item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    raise LookupError(f"no {kind} {name!r}")


def find_pin(tree, cell_name, pin_name):
    return next(p for p in find_node(tree, "CELL", cell_name)["pinsp"] if p["name"] == pin_name)


def buffer_design(child_stmts=None, top_stmts=None):
    """top -> u_buf : buffer, with ``in``/``out`` wired to ``x``/``y``.

    ``buffer`` is declared first, so the top module is not simply the first
    entry of ``modulesp``.
    """
    child = module(
        "(M1)",
        "buffer",
        var("(P_in)", "in", "INPUT"),
        var("(P_out)", "out", "OUTPUT"),
        *(child_stmts or []),
    )
    top = module(
        "(M0)",
        "top",
        var("(V_x)", "x"),
        var("(V_y)", "y"),
        cell(
            "u_buf",
            "(M1)",
            pin("in", "(P_in)", varref("(V_x)")),
            pin("out", "(P_out)", varref("(V_y)")),
        ),
        *(top_stmts or []),
    )
    return netlist(child, top)
