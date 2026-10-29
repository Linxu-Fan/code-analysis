"""Shared visual style for graphs (used by SVG export and the Qt graph view)."""

from __future__ import annotations

from . import model as M

# fill, border
KIND_STYLE = {
    M.FILE: ("#e8f1fb", "#4a78b5", "note"),
    M.NAMESPACE: ("#eef3e6", "#6b8e23", "rect"),
    M.PACKAGE: ("#eef3e6", "#6b8e23", "rect"),
    M.CLASS: ("#fff4d6", "#c08a00", "rect"),
    M.STRUCT: ("#fff4d6", "#c08a00", "rect"),
    M.UNION: ("#fff4d6", "#c08a00", "rect"),
    M.INTERFACE: ("#fff9e8", "#c08a00", "rect"),
    M.ENUM: ("#f6ecff", "#8a5cc2", "rect"),
    M.TYPEDEF: ("#f6ecff", "#8a5cc2", "rect"),
    M.FUNCTION: ("#e4f6ee", "#2e8b57", "round"),
    M.METHOD: ("#e4f6ee", "#2e8b57", "round"),
    M.CONSTRUCTOR: ("#e4f6ee", "#2e8b57", "round"),
    M.MACRO: ("#fde9e4", "#c0503a", "round"),
    M.VARIABLE: ("#f0f0f0", "#777777", "rect"),
    M.FIELD: ("#f0f0f0", "#777777", "rect"),
}
UNRESOLVED_STYLE = ("#fafafa", "#9a9a9a", "dashed")
FOCUS_BORDER = "#d9480f"

CFG_STYLE = {
    "start": ("#2e8b57", "#1f6b40", "ellipse"),
    "end": ("#555555", "#333333", "ellipse"),
    "block": ("#eef4fb", "#4a78b5", "rect"),
    "cond": ("#fff4d6", "#c08a00", "diamond"),
    "loop": ("#fde9e4", "#c0503a", "diamond"),
    "switch": ("#f6ecff", "#8a5cc2", "diamond"),
    "case": ("#f6ecff", "#8a5cc2", "round"),
    "return": ("#e4f6ee", "#2e8b57", "round"),
    "jump": ("#f0f0f0", "#777777", "round"),
    "try": ("#e8f1fb", "#4a78b5", "round"),
    "handler": ("#fde9e4", "#c0503a", "round"),
}

EDGE_COLORS = {
    "T": "#2e8b57",
    "F": "#c0503a",
    "exception": "#c0503a",
    M.INHERIT: "#c08a00",
    M.IMPLEMENT: "#c08a00",
    M.INCLUDE: "#4a78b5",
    M.IMPORT: "#4a78b5",
    M.CREATE: "#8a5cc2",
    "member": "#9a9a9a",
}


def node_style(kind: str):
    """(fill, border, shape) for a graph node kind."""
    if kind.startswith("cfg:"):
        return CFG_STYLE.get(kind[4:], CFG_STYLE["block"])
    if kind in M.UNRESOLVED_KINDS or kind == "unresolved":
        return UNRESOLVED_STYLE
    if kind == "dir":
        return ("#eef3e6", "#6b8e23", "note")
    return KIND_STYLE.get(kind, ("#f4f4f4", "#888888", "rect"))


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour *a* into *b* by factor *t* (0 = b, 1 = a)."""
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(ca, cb))


def node_style_dark(kind: str, bg: str = "#1e1f22"):
    """Dark-theme variant: tinted fills with bright borders, keeping each kind's hue."""
    fill, border, shape = node_style(kind)
    if kind in ("cfg:start", "cfg:end"):
        return fill, border, shape
    if shape == "dashed":
        return "#26282c", "#6e7681", shape
    return _mix(border, bg, 0.17), _mix(border, "#ffffff", 0.7), shape
