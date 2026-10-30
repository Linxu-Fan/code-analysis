"""Call trees: callers (ancestors) and/or callees (descendants) of a function as a real tree.

Unlike the call *graph*, every call path gets its own branch, so the tree reads like an
outline of the program flow.  Children are ordered as the calls appear in the source.
Recursion and repeated subtrees are marked instead of being expanded again, which keeps
trees of real code finite and readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from . import model as M

CALL_KINDS = (M.CALL, M.CREATE)
DOWN, UP = "down", "up"


@dataclass
class TreeNode:
    entity: object                 # database Entity
    depth: int                     # 0 = root; negative = ancestors; positive = descendants
    side: str = ""                 # "", DOWN or UP
    children: list = field(default_factory=list)
    call_line: int = 0             # line of the call site that links this node to its parent
    call_file: int | None = None
    recursive: bool = False        # calls something already on its own path
    repeated: bool = False         # subtree already expanded elsewhere in the tree
    truncated: bool = False        # more levels exist beyond the depth limit
    # layout results (centre coordinates)
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def label(self):
        e = self.entity
        if e.kind in M.UNRESOLVED_KINDS:
            return e.longname or e.name
        parts = e.longname.replace("::", ".").split(".")
        name = ".".join(parts[-2:]) if e.kind in (M.METHOD, M.CONSTRUCTOR) and len(parts) > 1 else e.name
        sep = "::" if e.lang in ("c", "cpp") else "."
        return name.replace(".", sep) if sep == "::" else name

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class CallTree:
    root: TreeNode
    up: list = field(default_factory=list)       # ancestor subtrees (children of root, going up)
    down: list = field(default_factory=list)     # descendant subtrees
    width: float = 0.0
    height: float = 0.0
    truncated_nodes: bool = False

    def nodes(self):
        yield self.root
        for c in self.up + self.down:
            yield from c.walk()

    @property
    def size(self):
        return sum(1 for _ in self.nodes())


def _callees(db, eid, hide_unresolved):
    """Distinct callees in source order: [(entity, line, file)]."""
    out, seen = [], set()
    for r in db.refs_from(eid, CALL_KINDS):
        if r.dst_id in seen:
            continue
        e = db.entity(r.dst_id)
        if e is None or (hide_unresolved and e.is_unresolved):
            continue
        if e.kind not in M.FUNCTION_KINDS and e.kind not in M.UNRESOLVED_KINDS and e.kind != M.MACRO \
                and e.kind not in M.CLASS_KINDS:
            continue
        seen.add(r.dst_id)
        out.append((e, r.line, r.file_id))
    return out


def _callers(db, eid):
    """Distinct calling functions ordered by file and line of the call site."""
    out, seen = [], set()
    for r in db.refs_to(eid, CALL_KINDS):
        e = db.entity(r.src_id)
        if e is None or e.id in seen:
            continue
        if e.kind not in M.FUNCTION_KINDS:
            # calls from file / class scope (e.g. Python module code) show the scope itself
            if e.kind not in (M.FILE, M.CLASS, M.STRUCT, M.NAMESPACE):
                continue
        seen.add(e.id)
        out.append((e, r.line, r.file_id))
    out.sort(key=lambda t: ((db.file(t[2]).relpath if db.file(t[2]) else ""), t[1]))
    return out


def build_call_tree(db, entity, descendants=True, ancestors=False, depth=5, hide_unresolved=False,
                    expand_repeated=False, max_nodes=2000):
    """Build the call tree of *entity* (a database Entity)."""
    root = TreeNode(entity, 0)
    tree = CallTree(root)
    budget = [max_nodes]

    def expand(node, path, side, level, expanded):
        if level >= depth:
            more = (_callees(db, node.entity.id, hide_unresolved) if side == DOWN
                    else _callers(db, node.entity.id))
            node.truncated = bool(more)
            return
        items = _callees(db, node.entity.id, hide_unresolved) if side == DOWN else _callers(db, node.entity.id)
        for e, line, fid in items:
            if budget[0] <= 0:
                node.truncated = True
                tree.truncated_nodes = True
                return
            budget[0] -= 1
            child = TreeNode(e, level + 1 if side == DOWN else -(level + 1), side, call_line=line, call_file=fid)
            node.children.append(child)
            if e.id in path:
                child.recursive = True
                continue
            if e.is_unresolved or (side == UP and e.kind not in M.FUNCTION_KINDS):
                continue
            if e.id in expanded and not expand_repeated:
                # already expanded elsewhere: mark it (only if there is something to show)
                child.repeated = bool(_callees(db, e.id, hide_unresolved) if side == DOWN else _callers(db, e.id))
                continue
            expanded.add(e.id)
            expand(child, path | {e.id}, side, level + 1, expanded)

    if descendants:
        expand(root, {entity.id}, DOWN, 0, {entity.id})
        tree.down = root.children
        root.children = []
    if ancestors:
        expand(root, {entity.id}, UP, 0, {entity.id})
        tree.up = root.children
        root.children = []
    root.children = []
    return tree


# ---- layout ------------------------------------------------------------------------------------

def default_measure(label, char_w=7.0, pad_x=14.0, height=26.0):
    return max(len(label) * char_w + 2 * pad_x, 46.0), height


def layout_call_tree(tree: CallTree, measure=default_measure, level_gap=56.0, sibling_gap=8.0, margin=24.0):
    """Tidy left-to-right layout: descendants grow to the right, ancestors to the left.

    Leaves are stacked in order without overlaps and every parent is centred on its
    children, giving a balanced, evenly distributed tree.
    """
    for n in tree.nodes():
        n.width, n.height = measure(_display(n))

    def stack(nodes, start_y):
        """Assign y to a list of sibling subtrees starting at *start_y*; returns end y."""
        y = start_y
        for n in nodes:
            if n.children:
                top = y
                y = stack(n.children, y)
                first, last = n.children[0], n.children[-1]
                n.y = (first.y + last.y) / 2
                # a parent taller than its children block must not overlap the next sibling
                y = max(y, n.y + n.height / 2 + sibling_gap, top + n.height + sibling_gap)
            else:
                n.y = y + n.height / 2
                y += n.height + sibling_gap
        return y

    def column_widths(nodes, widths, depth=1):
        for n in nodes:
            widths[depth] = max(widths.get(depth, 0), n.width)
            column_widths(n.children, widths, depth + 1)
        return widths

    down_h = stack(tree.down, 0.0) - sibling_gap if tree.down else 0.0
    up_h = stack(tree.up, 0.0) - sibling_gap if tree.up else 0.0
    root = tree.root
    total_h = max(down_h, up_h, root.height)
    # centre both sides vertically on the root
    for side, h in ((tree.down, down_h), (tree.up, up_h)):
        off = (total_h - h) / 2
        for top in side:
            for n in top.walk():
                n.y += off
    root.y = total_h / 2
    if tree.down:
        root.y = (tree.down[0].y + tree.down[-1].y) / 2 if not tree.up else root.y
    elif tree.up:
        root.y = (tree.up[0].y + tree.up[-1].y) / 2

    up_w = column_widths(tree.up, {})
    down_w = column_widths(tree.down, {})
    # x positions: column centres
    left_extent = sum(up_w.values()) + level_gap * len(up_w)
    root.x = left_extent + root.width / 2
    x = root.x + root.width / 2
    down_x = {}                       # left edge of each descendant column
    for d in sorted(down_w):
        x += level_gap
        down_x[d] = x
        x += down_w[d]
    x = root.x - root.width / 2
    up_x = {}                         # right edge of each ancestor column
    for d in sorted(up_w):
        x -= level_gap
        up_x[d] = x
        x -= up_w[d]

    def place(nodes, cols, depth, sign):
        for n in nodes:
            # descendants are left-aligned, ancestors right-aligned, so branches end on one line
            n.x = cols[depth] + sign * n.width / 2
            place(n.children, cols, depth + 1, sign)
    place(tree.down, down_x, 1, 1)
    place(tree.up, up_x, 1, -1)
    # shift everything into positive space with a margin
    min_x = min(n.x - n.width / 2 for n in tree.nodes())
    min_y = min(n.y - n.height / 2 for n in tree.nodes())
    for n in tree.nodes():
        n.x += margin - min_x
        n.y += margin - min_y
    tree.width = max(n.x + n.width / 2 for n in tree.nodes()) + margin
    tree.height = max(n.y + n.height / 2 for n in tree.nodes()) + margin
    tree.level_gap = level_gap
    return tree


def _display(n: TreeNode):
    label = n.label
    if n.recursive:
        label += "  ↻"
    elif n.repeated:
        label += "  …"
    elif n.truncated and not n.children:
        label += "  ▸"
    return label


def edges(tree: CallTree):
    """(parent, child, side) pairs; for ancestors the arrow points from caller to callee."""
    out = []

    def walk(parent, nodes, side):
        for c in nodes:
            out.append((parent, c, side))
            walk(c, c.children, side)
    walk(tree.root, tree.down, DOWN)
    walk(tree.root, tree.up, UP)
    return out


def connector(parent, child, side, level_gap=56.0, radius=6.0):
    """Orthogonal 'bus' connector as a list of path commands (left-to-right flow).

    All children of one parent share the vertical trunk half way through the level gap,
    so wide fans merge into a single, tidy branch.
    """
    if side == DOWN:
        x1, y1 = parent.x + parent.width / 2, parent.y
        x2, y2 = child.x - child.width / 2, child.y
        xm = x2 - level_gap / 2
    else:                     # caller (child, on the left) -> callee (parent)
        x1, y1 = child.x + child.width / 2, child.y
        x2, y2 = parent.x - parent.width / 2, parent.y
        xm = x1 + level_gap / 2
    if abs(y2 - y1) < 0.5:
        return [("M", x1, y1), ("L", x2, y2)]
    r = min(radius, abs(y2 - y1) / 2, abs(xm - x1), abs(x2 - xm))
    dy = 1 if y2 > y1 else -1
    return [("M", x1, y1), ("L", xm - r, y1), ("Q", xm, y1, xm, y1 + dy * r), ("L", xm, y2 - dy * r),
            ("Q", xm, y2, xm + r, y2), ("L", x2, y2)]


def path_d(cmds):
    out = []
    for c in cmds:
        out.append(c[0] + " ".join(f"{v:.1f}" for v in c[1:]))
    return " ".join(out)


# ---- styling shared by SVG export and the GUI ---------------------------------------------------

NODE_COLORS = {
    "root": ("#d9480f", "#ffffff", "#d9480f"),
    "function": ("#e4f6ee", "#1f2328", "#2e8b57"),
    "method": ("#e3f1f8", "#1f2328", "#2f7fa8"),
    "class": ("#fff4d6", "#1f2328", "#c08a00"),
    "macro": ("#fde9e4", "#1f2328", "#c0503a"),
    "unresolved": ("#f6f6f6", "#666666", "#9a9a9a"),
    "scope": ("#eef1f4", "#1f2328", "#6b7280"),
}


def node_category(n: TreeNode):
    k = n.entity.kind
    if n.depth == 0:
        return "root"
    if k in M.UNRESOLVED_KINDS:
        return "unresolved"
    if k in (M.METHOD, M.CONSTRUCTOR):
        return "method"
    if k == M.FUNCTION:
        return "function"
    if k in M.CLASS_KINDS:
        return "class"
    if k == M.MACRO:
        return "macro"
    return "scope"


def to_svg(tree: CallTree, title=""):
    if not tree.width:
        layout_call_tree(tree)
    w, h = tree.width, tree.height + (26 if title else 0)
    oy = 26 if title else 0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}" '
           f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12">',
           '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8b949e"/></marker></defs>',
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    if title:
        out.append(f'<text x="12" y="18" font-size="13" font-weight="600" fill="#1f2328">{escape(title)}</text>')
    out.append(f'<g transform="translate(0,{oy})">')
    for parent, child, side in edges(tree):
        dash = ' stroke-dasharray="4,3"' if child.entity.is_unresolved else ""
        out.append(f'<path d="{path_d(connector(parent, child, side))}" fill="none" stroke="#8b949e" '
                   f'stroke-width="1.3"{dash} marker-end="url(#ah)"/>')
    for n in tree.nodes():
        fill, fg, border = NODE_COLORS[node_category(n)]
        x, y = n.x - n.width / 2, n.y - n.height / 2
        dash = ' stroke-dasharray="4,3"' if (n.entity.is_unresolved or n.repeated) else ""
        tip = escape(n.entity.longname + (f"  (called at line {n.call_line})" if n.call_line else ""))
        out.append(f'<g><title>{tip}</title><rect x="{x:.1f}" y="{y:.1f}" width="{n.width:.1f}" '
                   f'height="{n.height:.1f}" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.3"{dash}/>'
                   f'<text x="{n.x:.1f}" y="{n.y + 4:.1f}" text-anchor="middle" fill="{fg}"'
                   f'{" font-weight=\"600\"" if n.depth == 0 else ""}>{escape(_display(n))}</text></g>')
    out.append("</g></svg>")
    return "\n".join(out)


def to_text(tree: CallTree):
    """Indented text rendering (for the command line)."""
    lines = []

    def walk(nodes, prefix, arrow):
        for i, n in enumerate(nodes):
            last = i == len(nodes) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{arrow}{_display(n)}"
                         + (f"   [line {n.call_line}]" if n.call_line else ""))
            walk(n.children, prefix + ("    " if last else "│   "), arrow)
    if tree.up:
        lines.append("Callers (ancestors):")
        lines.append(f"{tree.root.label}")
        walk(tree.up, "", "← ")
    if tree.down:
        if tree.up:
            lines.append("")
        lines.append("Callees (descendants):")
        lines.append(f"{tree.root.label}")
        walk(tree.down, "", "")
    if not tree.up and not tree.down:
        lines.append(f"{tree.root.label}   (no calls found)")
    lines.append("")
    lines.append("↻ recursion   … repeated subtree (shown once)   ▸ depth limit reached")
    return "\n".join(lines)
