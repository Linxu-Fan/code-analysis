"""Layered (Sugiyama style) directed graph layout, independent of any GUI toolkit."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class GNode:
    id: object
    label: str
    kind: str = ""
    width: float = 120.0
    height: float = 36.0
    x: float = 0.0          # centre
    y: float = 0.0          # centre
    data: object = None
    style: dict = field(default_factory=dict)


@dataclass
class GEdge:
    src: object
    dst: object
    label: str = ""
    kind: str = ""
    points: list = field(default_factory=list)   # polyline [(x, y), ...]
    reversed: bool = False


@dataclass
class Graph:
    title: str = ""
    nodes: dict = field(default_factory=dict)   # id -> GNode
    edges: list = field(default_factory=list)
    direction: str = "TB"                       # TB or LR
    width: float = 0.0
    height: float = 0.0
    clusters: dict = field(default_factory=dict)  # name -> [node ids]

    def add_node(self, nid, label, kind="", data=None, **style):
        if nid not in self.nodes:
            self.nodes[nid] = GNode(nid, label, kind, data=data, style=style)
        return self.nodes[nid]

    def add_edge(self, a, b, label="", kind=""):
        for e in self.edges:
            if e.src == a and e.dst == b and e.label == label:
                return e
        e = GEdge(a, b, label, kind)
        self.edges.append(e)
        return e


def measure(label: str, char_w=7.0, line_h=15.0, pad_x=18.0, pad_y=12.0):
    lines = label.split("\n") or [""]
    w = max(len(l) for l in lines) * char_w + 2 * pad_x
    h = len(lines) * line_h + 2 * pad_y
    return max(w, 50.0), max(h, 30.0)


def layout(g: Graph, rank_sep=60.0, node_sep=30.0, measure_fn=measure, iterations=12):
    """Assign coordinates to all nodes and route edges as polylines."""
    if not g.nodes:
        g.width = g.height = 0
        return g
    for n in g.nodes.values():
        n.width, n.height = measure_fn(n.label)
        if n.kind in ("cfg:cond", "cfg:loop", "cfg:switch"):      # text must fit inside a diamond
            n.width, n.height = n.width * 1.45 + 10, n.height * 1.35
        if g.direction == "LR":
            n.width, n.height = n.height, n.width
    ids = list(g.nodes)
    index = {nid: i for i, nid in enumerate(ids)}
    succ = defaultdict(list)
    edges = []
    for e in g.edges:
        if e.src not in index or e.dst not in index or e.src == e.dst:
            continue
        edges.append(e)
    # ---- 1. break cycles with a DFS ordering
    visited, onstack = set(), set()
    back = set()
    adj = defaultdict(list)
    for k, e in enumerate(edges):
        adj[e.src].append((e.dst, k))
    roots = [n for n in ids if not any(e.dst == n for e in edges)] or ids[:1]
    order = roots + [n for n in ids if n not in roots]
    for start in order:
        if start in visited:
            continue
        stack = [(start, iter(adj[start]))]
        visited.add(start)
        onstack.add(start)
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                onstack.discard(node)
                stack.pop()
                continue
            dst, k = nxt
            if dst in onstack:
                back.add(k)
            elif dst not in visited:
                visited.add(dst)
                onstack.add(dst)
                stack.append((dst, iter(adj[dst])))
    dag = []
    for k, e in enumerate(edges):
        e.reversed = k in back
        a, b = (e.dst, e.src) if e.reversed else (e.src, e.dst)
        dag.append((a, b, e))
        succ[a].append(b)
    # ---- 2. longest path layering
    indeg = defaultdict(int)
    for a, b, _ in dag:
        indeg[b] += 1
    rank = {n: 0 for n in ids}
    q = deque(n for n in ids if indeg[n] == 0)
    while q:
        n = q.popleft()
        for m in succ[n]:
            rank[m] = max(rank[m], rank[n] + 1)
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    # pull sources down next to their successors for compactness
    for n in ids:
        if succ[n] and not any(b == n for _, b, _ in dag):
            rank[n] = max(rank[n], min(rank[m] for m in succ[n]) - 1)
    # ---- 3. dummy nodes for long edges
    layers = defaultdict(list)
    for n in ids:
        layers[rank[n]].append(n)
    chains = []
    dummy_id = 0
    vsucc = defaultdict(list)
    vpred = defaultdict(list)
    vwidth = {n: (g.nodes[n].width if g.direction == "TB" else g.nodes[n].height) for n in ids}
    for a, b, e in dag:
        chain = [a]
        for r in range(rank[a] + 1, rank[b]):
            d = ("__dummy__", dummy_id)
            dummy_id += 1
            rank[d] = r
            layers[r].append(d)
            vwidth[d] = 8.0
            chain.append(d)
        chain.append(b)
        for x, y in zip(chain, chain[1:]):
            vsucc[x].append(y)
            vpred[y].append(x)
        chains.append((chain, e))
    nlayers = max(layers) + 1 if layers else 0
    order_of = {}
    for r in range(nlayers):
        for i, n in enumerate(layers[r]):
            order_of[n] = i

    # ---- 4. crossing reduction (barycentre sweeps)
    def sweep(down):
        rng = range(1, nlayers) if down else range(nlayers - 2, -1, -1)
        for r in rng:
            nb = vpred if down else vsucc
            def bary(n):
                ps = nb[n]
                if not ps:
                    return order_of[n]
                return sum(order_of[p] for p in ps) / len(ps)
            layers[r].sort(key=bary)
            for i, n in enumerate(layers[r]):
                order_of[n] = i
    for it in range(iterations):
        sweep(it % 2 == 0)
    # ---- 5. coordinate assignment
    pos = {}
    for r in range(nlayers):
        x = 0.0
        for n in layers[r]:
            w = vwidth[n]
            pos[n] = x + w / 2
            x += w + node_sep
    for it in range(8):
        for r in (range(nlayers) if it % 2 == 0 else range(nlayers - 1, -1, -1)):
            row = layers[r]
            want = {}
            for n in row:
                nb = vpred[n] + vsucc[n]
                want[n] = sum(pos[m] for m in nb) / len(nb) if nb else pos[n]
            # place left to right respecting separation, then shift to balance
            placed = []
            for n in row:
                x = want[n]
                if placed:
                    prev = placed[-1]
                    minx = pos[prev] + vwidth[prev] / 2 + node_sep + vwidth[n] / 2
                    x = max(x, minx)
                pos[n] = x
                placed.append(n)
            if row:
                shift = sum(want[n] - pos[n] for n in row) / len(row)
                # shifting left is allowed as long as the row keeps its order
                for n in row:
                    pos[n] += shift
    minx = min(pos[n] - vwidth[n] / 2 for n in pos) if pos else 0
    for n in pos:
        pos[n] -= minx
    # rank coordinates
    rank_size = []
    for r in range(nlayers):
        sz = 0.0
        for n in layers[r]:
            if n in g.nodes:
                gn = g.nodes[n]
                sz = max(sz, gn.height if g.direction == "TB" else gn.width)
        rank_size.append(max(sz, 10.0))
    rank_pos = []
    acc = 0.0
    for r in range(nlayers):
        rank_pos.append(acc + rank_size[r] / 2)
        acc += rank_size[r] + rank_sep
    margin = 20.0

    def xy(n):
        if g.direction == "TB":
            return pos[n] + margin, rank_pos[rank[n]] + margin
        return rank_pos[rank[n]] + margin, pos[n] + margin
    for n in ids:
        g.nodes[n].x, g.nodes[n].y = xy(n)
    for chain, e in chains:
        pts = [xy(n) for n in chain]
        if e.reversed:
            pts.reverse()
        # clip end points to node borders
        a, b = g.nodes[e.src], g.nodes[e.dst]
        pts[0] = _border(a, pts[1] if len(pts) > 1 else (b.x, b.y))
        pts[-1] = _border(b, pts[-2] if len(pts) > 1 else (a.x, a.y))
        e.points = pts
    for e in g.edges:
        if e.src == e.dst and e.src in g.nodes:
            n = g.nodes[e.src]
            r = n.x + n.width / 2
            e.points = [(r, n.y - 6), (r + 24, n.y - 14), (r + 24, n.y + 14), (r, n.y + 6)]
    g.width = max((n.x + n.width / 2 for n in g.nodes.values()), default=0) + margin
    g.height = max((n.y + n.height / 2 for n in g.nodes.values()), default=0) + margin
    return g


def _border(node, toward):
    """Point on the rectangle border of *node* in the direction of *toward*."""
    cx, cy = node.x, node.y
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = node.width / 2, node.height / 2
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s
