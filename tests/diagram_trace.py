"""Read a rendered block diagram back into connectivity.

The tracer only looks at characters, the way a person reads the drawing, so a
test built on it checks what the diagram shows rather than how the router
happened to produce it.
"""

import re

N, E, S, W = 1, 2, 4, 8
STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}
OPPOSITE = {N: S, S: N, E: W, W: E}
WIRES = {
    "│": N | S, "─": E | W,
    "┌": E | S, "┐": S | W, "└": N | E, "┘": N | W,
    "├": N | E | S, "┤": N | S | W, "┬": E | S | W, "┴": N | E | W,
}
NAME = re.compile(r"[^\s▶◆]+")
BITS = re.compile(r"\[-?\d+:-?\d+\]$")


def bare(name):
    """A port or net name without clock marker or range: "▷d[7:0]" is "d"."""
    return BITS.sub("", name.lstrip("▷"))


class Diagram:
    def __init__(self, text):
        lines = text.splitlines()
        width = max(map(len, lines), default=0)
        self.rows = [line.ljust(width) for line in lines]
        self.boxes = self._find_boxes()
        self.box_cells = {cell for box in self.boxes for cell in box["cells"]}
        self.pins = {}  # stub cell -> (instance, port)
        for box in self.boxes:
            self.pins.update(box["pins"])

    def char(self, x, y):
        if 0 <= y < len(self.rows) and 0 <= x < len(self.rows[y]):
            return self.rows[y][x]
        return " "

    def _find_boxes(self):
        boxes = []
        for y, row in enumerate(self.rows):
            for x, char in enumerate(row):
                if char != "┌":
                    continue
                x2 = x + 1
                while self.char(x2, y) == "─":
                    x2 += 1
                if self.char(x2, y) != "┐" or self.char(x, y + 1) != "│" or self.char(x2, y + 1) != "│":
                    continue
                # "u_proc : mux_procedural": the instance name comes first.
                title = self.rows[y + 1][x + 1 : x2].strip().split(" : ")[0]
                if not title:
                    continue
                y2 = y + 1
                while y2 < len(self.rows) and self.char(x, y2) in "│┤":
                    y2 += 1
                if self.rows[y2 : y2 + 1] != [] and self.rows[y2][x : x2 + 1] != "└" + "─" * (x2 - x - 1) + "┘":
                    continue
                if y2 >= len(self.rows):
                    continue
                pins = {}
                for r in range(y + 1, y2):
                    content = self.rows[r][x + 1 : x2]
                    if self.char(x, r) == "┤":
                        pins[(x - 1, r)] = (title, bare(content.split()[0]))
                    if self.char(x2, r) == "├":
                        pins[(x2 + 1, r)] = (title, bare(content.split()[-1]))
                cells = {(cx, cy) for cx in range(x, x2 + 1) for cy in range(y, y2 + 1)}
                boxes.append({"title": title, "x": x, "y": y, "pins": pins, "cells": cells})
        return boxes

    def box(self, title):
        return next(b for b in self.boxes if b["title"] == title)

    def _label(self, x, y, step):
        """The net name written beyond a wire end."""
        row = self.rows[y]
        if step == W:
            names = NAME.findall(row[: x + 1])
            return bare(names[-1]) if names else None
        if step == E:
            names = NAME.findall(row[x:])
            return bare(names[0]) if names else None
        return None

    def trace(self, start):
        """Every pin and label reachable from a stub cell, and any dangling end."""
        ends = set()
        seen = set()
        stack = [(start, None)]
        while stack:
            (x, y), heading = stack.pop()
            char = self.char(x, y)
            if char == "┼":
                if ((x, y), heading) in seen:
                    continue
                seen.add(((x, y), heading))
                steps = [heading]
            else:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                if (x, y) in self.pins:
                    ends.add(("pin",) + self.pins[(x, y)])
                bits = WIRES.get(char, 0)
                steps = [s for s in (N, E, S, W) if bits & s]
            for step in steps:
                dx, dy = STEP[step]
                nxt = (x + dx, y + dy)
                if nxt in self.box_cells:
                    continue  # the pin border this stub belongs to
                other = self.char(*nxt)
                if other == "┼":
                    stack.append((nxt, step))
                elif WIRES.get(other, 0) & OPPOSITE[step]:
                    stack.append((nxt, None))
                elif other == " ":
                    # A label may sit one space past the wire end.
                    beyond = (nxt[0] + dx, nxt[1] + dy)
                    if step in (E, W) and self.char(*beyond) not in " " and beyond not in self.box_cells:
                        ends.add(("label", self._label(*beyond, step)))
                    else:
                        ends.add(("dangling", nxt))
                else:
                    ends.add(("label", self._label(*nxt, step)))
        return frozenset(ends)

    def nets(self):
        """The connectivity drawn: one set of ends per component touching a pin."""
        return {self.trace(cell) for cell in self.pins if self.char(*cell) != "?"}
