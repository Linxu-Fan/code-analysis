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
    weight: float = 1.0                          # e.g. number of references (drawn as thickness)


@dataclass
class Graph:
    title: str = ""
    nodes: dict = field(default_factory=dict)   # id -> GNode
    edges: list = field(default_factory=list)
    direction: str = "TB"                       # TB or LR
    width: float = 0.0
    height: float = 0.0
    clusters: dict = field(default_factory=dict)  # name -> [node ids]
    note: str = ""                                # e.g. what was left out to keep the graph readable

    def add_node(self, nid, label, kind="", data=None, **style):
        if nid not in self.nodes:
            self.nodes[nid] = GNode(nid, label, kind, data=data, style=style)
        return self.nodes[nid]

    def add_edge(self, a, b, label="", kind="", weight=1.0):
        for e in self.edges:
            if e.src == a and e.dst == b and e.label == label:
                return e
        e = GEdge(a, b, label, kind, weight=weight)
        self.edges.append(e)
        return e


def measure(label: str, char_w=7.0, line_h=15.0, pad_x=18.0, pad_y=12.0):
    lines = label.split("\n") or [""]
    w = max(len(l) for l in lines) * char_w + 2 * pad_x
    h = len(lines) * line_h + 2 * pad_y
    return max(w, 50.0), max(h, 30.0)


def _crossings(upper, lower, down_edges, pos):
    """Number of edge crossings between two adjacent layers (inversion count)."""
    pairs = []
    for u in upper:
        for v in down_edges.get(u, ()):
            pairs.append((pos[u], pos[v]))
    pairs.sort()
    # count inversions of the lower positions with a merge sort
    arr = [b for _, b in pairs]

    def sort_count(xs):
        if len(xs) < 2:
            return xs, 0
        mid = len(xs) // 2
        left, a = sort_count(xs[:mid])
        right, b = sort_count(xs[mid:])
        out, i, j, inv = [], 0, 0, a + b
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                out.append(left[i])
                i += 1
            else:
                out.append(right[j])
                inv += len(left) - i
                j += 1
        out += left[i:]
        out += right[j:]
        return out, inv
    return sort_count(arr)[1]


def _median(values):
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return None
    if n % 2:
        return vs[n // 2]
    if n == 2:
        return (vs[0] + vs[1]) / 2
    # weighted median (Gansner et al.) keeps nodes close to the denser side
    left = vs[n // 2 - 1] - vs[0]
    right = vs[-1] - vs[n // 2]
    if left + right == 0:
        return (vs[n // 2 - 1] + vs[n // 2]) / 2
    return (vs[n // 2 - 1] * right + vs[n // 2] * left) / (left + right)


def layout(g: Graph, rank_sep=60.0, node_sep=30.0, measure_fn=measure, iterations=24):
    """Assign coordinates to all nodes and route edges as polylines.

    Classic layered drawing: cycle removal, longest-path layering with rank tightening,
    dummy nodes for long edges, median + transpose crossing reduction, and balanced
    priority-based coordinate assignment that keeps long edges straight.
    """
    if not g.nodes:
        g.width = g.height = 0
        return g
    tb = g.direction == "TB"
    for n in g.nodes.values():
        n.width, n.height = measure_fn(n.label)
        if n.kind in ("cfg:cond", "cfg:loop", "cfg:switch"):      # text must fit inside a diamond
            n.width, n.height = n.width * 1.45 + 10, n.height * 1.35
    ids = list(g.nodes)
    index = {nid: i for i, nid in enumerate(ids)}
    edges = [e for e in g.edges if e.src in index and e.dst in index and e.src != e.dst]

    # ---- 1. break cycles (DFS from sources, in insertion order)
    adj = defaultdict(list)
    for k, e in enumerate(edges):
        adj[e.src].append((e.dst, k))
    has_in = {e.dst for e in edges}
    order = [n for n in ids if n not in has_in] + [n for n in ids if n in has_in]
    visited, onstack, back = set(), set(), set()
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
    succ, pred = defaultdict(list), defaultdict(list)
    seen_pairs = set()
    for k, e in enumerate(edges):
        e.reversed = k in back
        a, b = (e.dst, e.src) if e.reversed else (e.src, e.dst)
        dag.append((a, b, e))
        if (a, b) not in seen_pairs:
            seen_pairs.add((a, b))
            succ[a].append(b)
            pred[b].append(a)

    # ---- 2. layering: longest path, then pull nodes towards their neighbours
    indeg = {n: len(pred[n]) for n in ids}
    rank = {n: 0 for n in ids}
    q = deque(n for n in ids if indeg[n] == 0)
    while q:
        n = q.popleft()
        for m in succ[n]:
            rank[m] = max(rank[m], rank[n] + 1)
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    for _ in range(4):
        moved = False
        for n in ids:
            lo = max((rank[p] + 1 for p in pred[n]), default=None)
            hi = min((rank[s] - 1 for s in succ[n]), default=None)
            if lo is None and hi is None:
                continue
            if lo is None:
                target = hi            # sources sit right above their first successor
            elif hi is None:
                target = lo            # sinks right below their predecessors
            else:
                if lo > hi:
                    continue
                want = _median([rank[p] + 1 for p in pred[n]] + [rank[s] - 1 for s in succ[n]])
                target = int(round(min(max(want, lo), hi)))
            if target != rank[n] and target >= 0:
                rank[n] = target
                moved = True
        if not moved:
            break
    base = min(rank.values())
    for n in ids:
        rank[n] -= base

    # ---- 3. dummy nodes for edges spanning several ranks
    nlayers = max(rank.values()) + 1
    layers = [[] for _ in range(nlayers)]
    for n in ids:
        layers[rank[n]].append(n)
    vsucc, vpred = defaultdict(list), defaultdict(list)
    size = {n: (g.nodes[n].width if tb else g.nodes[n].height) for n in ids}
    is_dummy = set()
    chains = []
    dummy_id = 0
    for a, b, e in dag:
        chain = [a]
        for r in range(rank[a] + 1, rank[b]):
            d = ("__dummy__", dummy_id)
            dummy_id += 1
            rank[d] = r
            layers[r].append(d)
            size[d] = 4.0
            is_dummy.add(d)
            chain.append(d)
        chain.append(b)
        for x, y in zip(chain, chain[1:]):
            vsucc[x].append(y)
            vpred[y].append(x)
        chains.append((chain, e))

    # ---- 4. crossing reduction: initial DFS order, median sweeps, transpose, keep best
    placed = set()
    init_layers = [[] for _ in range(nlayers)]
    for r in range(nlayers):
        for n in layers[r]:
            if n in placed:
                continue
            stack = [n]
            while stack:
                x = stack.pop()
                if x in placed:
                    continue
                placed.add(x)
                init_layers[rank[x]].append(x)
                stack.extend(reversed(vsucc[x]))
    layers = init_layers
    pos = {}

    def renumber():
        for row in layers:
            for i, n in enumerate(row):
                pos[n] = i

    def total_crossings():
        return sum(_crossings(layers[r], layers[r + 1], vsucc, pos) for r in range(nlayers - 1))

    def transpose():
        improved = True
        rounds = 0
        while improved and rounds < 6:
            improved = False
            rounds += 1
            for r in range(nlayers):
                row = layers[r]
                if len(row) > 400:
                    continue
                for i in range(len(row) - 1):
                    v, w = row[i], row[i + 1]
                    before = 0
                    after = 0
                    for nb in (vpred, vsucc):
                        pv = [pos[x] for x in nb[v]]
                        pw = [pos[x] for x in nb[w]]
                        for a in pv:
                            for b in pw:
                                if a > b:
                                    before += 1
                                elif a < b:
                                    after += 1
                    if after < before:
                        row[i], row[i + 1] = w, v
                        pos[v], pos[w] = i + 1, i
                        improved = True

    renumber()
    best = [list(r) for r in layers]
    best_c = total_crossings()
    for it in range(iterations):
        down = it % 2 == 0
        rng = range(1, nlayers) if down else range(nlayers - 2, -1, -1)
        nb = vpred if down else vsucc
        for r in rng:
            row = layers[r]
            keyed = []
            for i, n in enumerate(row):
                m = _median([pos[x] for x in nb[n]])
                keyed.append((i if m is None else m, i, n))
            keyed.sort()
            layers[r] = [n for _, _, n in keyed]
            for i, n in enumerate(layers[r]):
                pos[n] = i
        transpose()
        c = total_crossings()
        if c < best_c:
            best_c = c
            best = [list(r) for r in layers]
        if best_c == 0:
            break
    layers = best
    renumber()

    # ---- 5. coordinates within ranks
    def sep(a, b):
        gap = node_sep if (a not in is_dummy and b not in is_dummy) else node_sep / 3
        return size[a] / 2 + gap + size[b] / 2

    x = {}
    for row in layers:
        cur = 0.0
        for i, n in enumerate(row):
            if i:
                cur += sep(row[i - 1], n)
            x[n] = cur

    def prio(n, nbrs):
        if n in is_dummy:
            return 10_000                       # keep long edges straight
        return len(nbrs)

    def place_row(row, want):
        """Priority method: move nodes towards *want* in order of priority without overlaps."""
        order = sorted(range(len(row)), key=lambda i: -want[row[i]][1])
        fixed = [False] * len(row)
        for i in order:
            n = row[i]
            target = want[n][0]
            if target is None:
                fixed[i] = True
                continue
            # bounds from fixed neighbours (higher priority already placed)
            lo, hi = -1e18, 1e18
            for j in range(i - 1, -1, -1):
                if fixed[j]:
                    lo = x[row[j]] + sum(sep(row[k], row[k + 1]) for k in range(j, i))
                    break
            for j in range(i + 1, len(row)):
                if fixed[j]:
                    hi = x[row[j]] - sum(sep(row[k], row[k + 1]) for k in range(i, j))
                    break
            nx = min(max(target, lo), hi) if lo <= hi else (lo + hi) / 2
            x[n] = nx
            fixed[i] = True
            # push unfixed neighbours out of the way
            for j in range(i + 1, len(row)):
                if fixed[j]:
                    break
                need = x[row[j - 1]] + sep(row[j - 1], row[j])
                if x[row[j]] < need:
                    x[row[j]] = need
            for j in range(i - 1, -1, -1):
                if fixed[j]:
                    break
                need = x[row[j + 1]] - sep(row[j], row[j + 1])
                if x[row[j]] > need:
                    x[row[j]] = need

    for it in range(8):
        down = it % 2 == 0
        rng = range(1, nlayers) if down else range(nlayers - 2, -1, -1)
        for r in rng:
            row = layers[r]
            want = {}
            for n in row:
                nbrs = vpred[n] if down else vsucc[n]
                m = _median([x[p] for p in nbrs])
                want[n] = (m, prio(n, nbrs))
            place_row(row, want)
    # final balancing pass using both neighbour layers
    for r in range(nlayers):
        row = layers[r]
        want = {}
        for n in row:
            nbrs = vpred[n] + vsucc[n]
            m = _median([x[p] for p in nbrs])
            want[n] = (m, prio(n, nbrs))
        place_row(row, want)
    minx = min(x[n] - size[n] / 2 for n in x)
    for n in x:
        x[n] -= minx

    # ---- 6. rank coordinates and edge routing
    rank_size = []
    for r in range(nlayers):
        sz = 10.0
        for n in layers[r]:
            if n in g.nodes:
                gn = g.nodes[n]
                sz = max(sz, gn.height if tb else gn.width)
        rank_size.append(sz)
    rank_pos = []
    acc = 0.0
    gap = rank_sep if tb else rank_sep * 1.6
    for r in range(nlayers):
        rank_pos.append(acc + rank_size[r] / 2)
        acc += rank_size[r] + gap
    margin = 20.0

    def xy(n):
        if tb:
            return x[n] + margin, rank_pos[rank[n]] + margin
        return rank_pos[rank[n]] + margin, x[n] + margin
    for n in ids:
        g.nodes[n].x, g.nodes[n].y = xy(n)
    for chain, e in chains:
        pts = [xy(n) for n in chain]
        if e.reversed:
            pts.reverse()
        a, b = g.nodes[e.src], g.nodes[e.dst]
        # leave/enter nodes through the side facing the next rank
        if len(pts) >= 2:
            pts[0] = _border(a, pts[1])
            pts[-1] = _border(b, pts[-2])
        # collapse collinear dummy points so straight runs stay straight
        clean = [pts[0]]
        for i in range(1, len(pts) - 1):
            p0, p1, p2 = clean[-1], pts[i], pts[i + 1]
            if abs((p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])) > 1.0:
                clean.append(p1)
        clean.append(pts[-1])
        e.points = clean
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
