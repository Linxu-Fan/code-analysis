"""Render a laid-out Graph to standalone SVG."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .layout import Graph, layout
from .style import EDGE_COLORS, FOCUS_BORDER, node_style

FONT = "Segoe UI, Helvetica, Arial, sans-serif"
MONO = "Consolas, Menlo, monospace"


def _path(points):
    if not points:
        return ""
    if len(points) == 2:
        (x1, y1), (x2, y2) = points
        return f"M{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f}"
    d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for i in range(1, len(points) - 1):
        x, y = points[i]
        nx, ny = points[i + 1]
        mx, my = (x + nx) / 2, (y + ny) / 2
        d += f" Q{x:.1f},{y:.1f} {mx:.1f},{my:.1f}" if i < len(points) - 2 else f" Q{x:.1f},{y:.1f} {nx:.1f},{ny:.1f}"
    return d


def to_svg(g: Graph, links=None) -> str:
    """*links*: optional {node data: href} for clickable nodes (HTML reports)."""
    if not g.width:
        layout(g)
    w, h = max(g.width, 100), max(g.height, 60) + 24
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
           f'viewBox="0 0 {w:.0f} {h:.0f}" font-family="{FONT}" font-size="12">',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>'
           '<marker id="tri" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="11" markerHeight="11" '
           'orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="#ffffff" stroke="#c08a00"/></marker></defs>',
           f'<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="10" y="{h - 8:.0f}" fill="#666" font-size="11">{escape(g.title + (" - " + g.note if g.note else ""))}</text>']
    import math
    maxw = max((e.weight for e in g.edges), default=1.0)
    for e in g.edges:
        color = EDGE_COLORS.get(e.kind, EDGE_COLORS.get(e.label, "#6b7280"))
        width = 1.3 + (2.7 * math.log1p(e.weight) / math.log1p(maxw) if maxw > 1 else 0)
        marker = "tri" if e.kind in ("Inherit", "Implement") else "arrow"
        dash = ' stroke-dasharray="5,4"' if e.kind in ("assoc", "Implement") else ""
        out.append(f'<path d="{_path(e.points)}" fill="none" stroke="{color}" stroke-width="{width:.1f}"{dash} '
                   f'marker-end="url(#{marker})"/>')
        if e.label and e.points:
            mid = e.points[len(e.points) // 2] if len(e.points) > 2 else (
                (e.points[0][0] + e.points[-1][0]) / 2, (e.points[0][1] + e.points[-1][1]) / 2)
            out.append(f'<text x="{mid[0] + 4:.1f}" y="{mid[1] - 3:.1f}" fill="{color}" font-size="10">'
                       f'{escape(e.label)}</text>')
    for n in g.nodes.values():
        fill, border, shape = node_style(n.kind)
        if n.style.get("focus"):
            border = FOCUS_BORDER
        x, y = n.x - n.width / 2, n.y - n.height / 2
        sw = 2.2 if n.style.get("focus") else 1.2
        dash = ' stroke-dasharray="4,3"' if shape == "dashed" else ""
        text_fill = "#ffffff" if n.kind in ("cfg:start", "cfg:end") else "#1f2328"
        if shape == "diamond":
            pts = f"{n.x:.1f},{y:.1f} {x + n.width:.1f},{n.y:.1f} {n.x:.1f},{y + n.height:.1f} {x:.1f},{n.y:.1f}"
            el = f'<polygon points="{pts}" fill="{fill}" stroke="{border}" stroke-width="{sw}"/>'
        elif shape == "ellipse":
            el = (f'<ellipse cx="{n.x:.1f}" cy="{n.y:.1f}" rx="{n.width / 2:.1f}" ry="{n.height / 2:.1f}" '
                  f'fill="{fill}" stroke="{border}" stroke-width="{sw}"/>')
        else:
            rx = 10 if shape == "round" else 2
            el = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{n.width:.1f}" height="{n.height:.1f}" rx="{rx}" '
                  f'fill="{fill}" stroke="{border}" stroke-width="{sw}"{dash}/>')
        lines = n.label.split("\n")
        uml = n.style.get("uml")
        line_h = 15
        ty = n.y - (len(lines) - 1) * line_h / 2 + 4
        texts = []
        for i, ln in enumerate(lines):
            if uml and ln.startswith("\u2500"):
                yy = ty + i * line_h - 4
                texts.append(f'<line x1="{x:.1f}" y1="{yy:.1f}" x2="{x + n.width:.1f}" y2="{yy:.1f}" stroke="{border}"/>')
                continue
            anchor = "middle" if (not uml or i == 0) else "start"
            tx = n.x if anchor == "middle" else x + 8
            weight = ' font-weight="600"' if (uml and i == 0) or (not uml and len(lines) == 1 and n.kind.startswith(("Class", "Struct"))) else ""
            fam = f' font-family="{MONO}" font-size="11"' if (n.kind.startswith("cfg:") or (uml and i > 0)) else ""
            texts.append(f'<text x="{tx:.1f}" y="{ty + i * line_h:.1f}" text-anchor="{anchor}" fill="{text_fill}"'
                         f'{weight}{fam}>{escape(ln)}</text>')
        body = el + "".join(texts)
        href = links.get(n.data) if links else None
        if href:
            body = f'<a href="{escape(href)}">{body}</a>'
        out.append(f"<g><title>{escape(n.label.splitlines()[0])}</title>{body}</g>")
    out.append("</svg>")
    return "\n".join(out)


def to_dot(g: Graph) -> str:
    """Graphviz DOT text of a graph (for users who prefer Graphviz rendering)."""
    def q(s):
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    lines = [f"digraph {q(g.title or 'graph')} {{", f"  rankdir={g.direction};",
             '  node [fontname="Helvetica", fontsize=10, style=filled];']
    for n in g.nodes.values():
        fill, border, shape = node_style(n.kind)
        dshape = {"diamond": "diamond", "ellipse": "ellipse", "round": "box", "note": "note"}.get(shape, "box")
        lines.append(f"  {q(n.id)} [label={q(n.label)}, shape={dshape}, fillcolor={q(fill)}, color={q(border)}];")
    for e in g.edges:
        attrs = f" [label={q(e.label)}]" if e.label else ""
        lines.append(f"  {q(e.src)} -> {q(e.dst)}{attrs};")
    lines.append("}")
    return "\n".join(lines)
