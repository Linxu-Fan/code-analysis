"""Builders for the graph views (call trees, inheritance, includes, dependencies, CFG, UML)."""

from __future__ import annotations

import posixpath
from collections import defaultdict

from . import model as M
from .layout import Graph

GRAPH_TYPES = {
    "calls": "Calls",
    "callby": "Called By",
    "butterfly": "Butterfly",
    "inheritance": "Inheritance",
    "uml": "UML Class Diagram",
    "includes": "Includes",
    "includedby": "Included By",
    "filedeps": "File Dependencies",
    "control": "Control Flow",
}


def graphs_for(entity) -> list[str]:
    """Graph types applicable to an entity, in menu order."""
    k = entity.kind
    if k in M.FUNCTION_KINDS:
        return ["calls", "callby", "butterfly", "control"]
    if k in M.CLASS_KINDS:
        return ["inheritance", "uml", "butterfly"]
    if k == M.FILE:
        return ["includes", "includedby", "filedeps", "uml"]
    if k == M.MACRO or k in M.UNRESOLVED_KINDS:
        return ["callby"]
    if k in (M.NAMESPACE, M.PACKAGE):
        return ["uml"]
    return ["butterfly"]


def _label(e, qualified=False):
    if e.kind in M.UNRESOLVED_KINDS:
        return e.longname or e.name
    if qualified:
        return e.longname
    if e.kind in M.FUNCTION_KINDS:
        p = e.longname.replace("::", ".").split(".")
        return ".".join(p[-2:]) if len(p) > 1 else e.name
    return e.name


def _node(g, e, focus=False):
    n = g.add_node(e.id, _label(e), e.kind, data=e.id)
    if focus:
        n.style["focus"] = True
    return n


def call_graph(db, ent, depth=3, reverse=False, hide_unresolved=False, max_nodes=250):
    g = Graph(title=f"{'Called By' if reverse else 'Calls'}: {ent.longname}")
    kinds = (M.CALL, M.CREATE)
    _node(g, ent, True)
    frontier = [ent.id]
    seen = {ent.id}
    for _ in range(depth):
        nxt = []
        for eid in frontier:
            for other in db.related(eid, kinds, reverse=reverse):
                if hide_unresolved and other.is_unresolved:
                    continue
                if len(g.nodes) >= max_nodes and other.id not in seen:
                    g.title += "  (truncated)"
                    continue
                _node(g, other)
                if reverse:
                    g.add_edge(other.id, eid)
                else:
                    g.add_edge(eid, other.id)
                if other.id not in seen:
                    seen.add(other.id)
                    if other.kind in M.FUNCTION_KINDS:
                        nxt.append(other.id)
        frontier = nxt
        if not frontier:
            break
    return g


def butterfly(db, ent, depth=1, hide_unresolved=False):
    g = Graph(title=f"Butterfly: {ent.longname}", direction="LR")
    if ent.kind in M.FUNCTION_KINDS or ent.kind == M.MACRO or ent.is_unresolved:
        kinds = (M.CALL, M.CREATE)
    elif ent.kind in M.CLASS_KINDS:
        kinds = (M.CALL, M.CREATE, M.USE, M.TYPED, M.INHERIT, M.IMPLEMENT)
    else:
        kinds = (M.USE, M.SET, M.CALL)
    _node(g, ent, True)
    for rev in (False, True):
        frontier = [ent.id]
        seen = {ent.id}
        for _ in range(depth):
            nxt = []
            for eid in frontier:
                for other in db.related(eid, kinds, reverse=rev):
                    if hide_unresolved and other.is_unresolved:
                        continue
                    if other.kind in (M.FILE,) and ent.kind in M.CLASS_KINDS:
                        continue
                    _node(g, other)
                    if rev:
                        g.add_edge(other.id, eid)
                    else:
                        g.add_edge(eid, other.id)
                    if other.id not in seen:
                        seen.add(other.id)
                        nxt.append(other.id)
            frontier = nxt
    return g


def inheritance_graph(db, ent, hide_unresolved=False):
    g = Graph(title=f"Inheritance: {ent.longname}")
    kinds = (M.INHERIT, M.IMPLEMENT)
    _node(g, ent, True)
    stack = [ent.id]
    seen = {ent.id}
    while stack:                       # ancestors (edges child -> base)
        eid = stack.pop()
        for b in db.related(eid, kinds):
            if hide_unresolved and b.is_unresolved:
                continue
            _node(g, b)
            g.add_edge(eid, b.id, kind=M.INHERIT)
            if b.id not in seen:
                seen.add(b.id)
                stack.append(b.id)
    stack = [ent.id]
    seen = {ent.id}
    while stack:                       # descendants
        eid = stack.pop()
        for d in db.related(eid, kinds, reverse=True):
            _node(g, d)
            g.add_edge(d.id, eid, kind=M.INHERIT)
            if d.id not in seen and len(g.nodes) < 300:
                seen.add(d.id)
                stack.append(d.id)
    g.direction = "TB"
    return g


def _uml_label(db, cls, max_members=14):
    lines = [f"\u00ab{cls.kind.lower()}\u00bb {cls.name}" if cls.kind != M.CLASS else cls.name, "\u2500" * 6]
    fields, methods = [], []
    vis = {"public": "+", "private": "-", "protected": "#"}
    for m in db.children(cls.id):
        mods = m.modifiers.split()
        mark = next((vis[x] for x in mods if x in vis), "+" if not m.name.startswith("_") else "-")
        if m.kind in (M.FIELD, M.PROPERTY, M.ENUMERATOR):
            fields.append(f"{mark} {m.name}" + (f" : {m.type}" if m.type else ""))
        elif m.kind in M.FUNCTION_KINDS:
            nparams = len(db.children(m.id, [M.PARAMETER]))
            methods.append(f"{mark} {m.name}({'\u2026' if nparams else ''})" + (f" : {m.type}" if m.type and m.type != "void" else ""))
    body = fields[:max_members]
    if len(fields) > max_members:
        body.append(f"\u2026 {len(fields) - max_members} more")
    body.append("\u2500" * 6)
    body += methods[:max_members]
    if len(methods) > max_members:
        body.append(f"\u2026 {len(methods) - max_members} more")
    return "\n".join(lines + body)


def uml_graph(db, ent=None, max_classes=60):
    """UML-style class diagram for a class (with neighbours), a file, namespace or the whole project."""
    if ent is None:
        classes = db.entities(kinds=sorted(M.CLASS_KINDS))
        title = "UML Class Diagram: project"
    elif ent.kind in M.CLASS_KINDS:
        classes = [ent] + [b for b in db.related(ent.id, (M.INHERIT, M.IMPLEMENT)) if not b.is_unresolved] + \
            [d for d in db.related(ent.id, (M.INHERIT, M.IMPLEMENT), reverse=True)]
        title = f"UML Class Diagram: {ent.longname}"
    elif ent.kind == M.FILE:
        classes = [e for e in db.file_outline(ent.file_id) if e.kind in M.CLASS_KINDS]
        title = f"UML Class Diagram: {ent.name}"
    else:
        classes = []
        stack = [ent.id]
        while stack:
            x = stack.pop()
            for ch in db.children(x):
                if ch.kind in M.CLASS_KINDS:
                    classes.append(ch)
                elif ch.kind in (M.NAMESPACE, M.PACKAGE):
                    stack.append(ch.id)
        title = f"UML Class Diagram: {ent.longname}"
    g = Graph(title=title)
    ids = []
    for c in classes:
        if c.id in g.nodes:
            continue
        if len(g.nodes) >= max_classes:
            g.title += "  (truncated)"
            break
        n = g.add_node(c.id, _uml_label(db, c), c.kind, data=c.id, uml=True)
        if ent is not None and c.id == ent.id:
            n.style["focus"] = True
        ids.append(c.id)
    if not ids:
        g.add_node("empty", "No classes in this scope", "unresolved")
    idset = set(ids)
    for cid in ids:
        for b in db.related(cid, (M.INHERIT, M.IMPLEMENT)):
            if b.id in idset:
                g.add_edge(cid, b.id, kind=M.INHERIT)
        # associations through typed fields
        for f in db.children(cid, [M.FIELD, M.PROPERTY]):
            for r in db.refs_from(f.id, [M.TYPED]):
                if r.dst_id in idset and r.dst_id != cid:
                    g.add_edge(cid, r.dst_id, kind="assoc", label=f.name)
        for r in db.refs_from(cid, [M.TYPED]):
            if r.dst_id in idset and r.dst_id != cid:
                g.add_edge(cid, r.dst_id, kind="assoc")
    return g


def include_graph(db, file_ent, depth=4, reverse=False):
    g = Graph(title=f"{'Included By' if reverse else 'Includes'}: {file_ent.longname}")
    kinds = (M.INCLUDE, M.IMPORT)
    _node(g, file_ent, True)
    frontier = [file_ent.id]
    seen = {file_ent.id}
    for _ in range(depth):
        nxt = []
        for eid in frontier:
            for other in db.related(eid, kinds, reverse=reverse):
                if other.kind not in (M.FILE, M.UNRESOLVED_INCLUDE, M.UNRESOLVED_MODULE):
                    # python "from x import y" targets: show the defining file
                    f = db.file_entity(other.file_id) if other.file_id else None
                    if f is None:
                        continue
                    other = f
                n = _node(g, other)
                n.label = other.longname if other.kind == M.FILE else other.name
                if reverse:
                    g.add_edge(other.id, eid, kind=M.INCLUDE)
                else:
                    g.add_edge(eid, other.id, kind=M.INCLUDE)
                if other.id not in seen and other.kind == M.FILE:
                    seen.add(other.id)
                    nxt.append(other.id)
        frontier = nxt
    g.nodes[file_ent.id].label = file_ent.longname
    return g


def file_dependencies(db):
    """{src file id: {dst file id: count}} aggregated from all resolved references."""
    deps = defaultdict(lambda: defaultdict(int))
    q = db.conn.execute("SELECT r.file_id, e.file_id, COUNT(*) FROM refs r JOIN entities e ON e.id=r.dst_id "
                        "WHERE e.file_id IS NOT NULL AND e.file_id != r.file_id GROUP BY r.file_id, e.file_id")
    for a, b, n in q:
        deps[a][b] += n
    return deps


def file_dependency_graph(db, file_ent=None, max_nodes=150):
    deps = file_dependencies(db)
    g = Graph(title="File Dependencies" + (f": {file_ent.longname}" if file_ent else ""), direction="LR")
    pairs = []
    if file_ent is not None:
        fid = file_ent.file_id
        for b, n in deps.get(fid, {}).items():
            pairs.append((fid, b, n))
        for a, m in deps.items():
            if fid in m:
                pairs.append((a, fid, m[fid]))
    else:
        for a, m in deps.items():
            for b, n in m.items():
                pairs.append((a, b, n))
    for a, b, n in pairs:
        for fid in (a, b):
            if fid not in g.nodes and len(g.nodes) < max_nodes:
                f = db.file(fid)
                g.add_node(fid, f.relpath, M.FILE, data=f.entity_id)
        if a in g.nodes and b in g.nodes:
            g.add_edge(a, b, label=str(n))
    if file_ent is not None and file_ent.file_id in g.nodes:
        g.nodes[file_ent.file_id].style["focus"] = True
    elif file_ent is not None:
        g.add_node(file_ent.file_id, file_ent.longname, M.FILE, data=file_ent.id, focus=True)
    return g


def directory_dependencies(db, level=None):
    """Aggregate file dependencies to directories: {(dir a, dir b): count}."""
    deps = file_dependencies(db)
    rel = {f.id: f.relpath for f in db.files()}

    def d(fid):
        p = posixpath.dirname(rel.get(fid, ""))
        if level is not None:
            p = "/".join(p.split("/")[:level])
        return p or "."
    out = defaultdict(int)
    for a, m in deps.items():
        for b, n in m.items():
            da, dbb = d(a), d(b)
            if da != dbb:
                out[(da, dbb)] += n
    return out


def architecture_graph(db, level=None):
    g = Graph(title="Architecture (directory dependencies)", direction="LR")
    dirs = {posixpath.dirname(f.relpath) or "." for f in db.files()}
    if level is not None:
        dirs = {"/".join(x.split("/")[:level]) or "." for x in dirs}
    for x in sorted(dirs):
        g.add_node(x, x + "/", "dir")
    for (a, b), n in directory_dependencies(db, level).items():
        g.add_edge(a, b, label=str(n))
    return g


def control_flow_graph(db, ent):
    from .cfg import build_cfg
    cfg = build_cfg(db, ent)
    g = Graph(title=f"Control Flow: {ent.longname}")
    if cfg is None:
        g.add_node("none", "control flow unavailable", "cfg:jump")
        return g
    for n in cfg.nodes:
        label = n.label
        g.add_node(n.id, label, "cfg:" + n.kind, data=("line", n.line))
    for a, b, lbl in cfg.edges:
        g.add_edge(a, b, label=lbl, kind=lbl)
    return g


def build_graph(db, kind, ent, depth=3, hide_unresolved=False):
    if kind == "calls":
        return call_graph(db, ent, depth, False, hide_unresolved)
    if kind == "callby":
        return call_graph(db, ent, depth, True, hide_unresolved)
    if kind == "butterfly":
        return butterfly(db, ent, max(1, min(depth, 3)), hide_unresolved)
    if kind == "inheritance":
        return inheritance_graph(db, ent, hide_unresolved)
    if kind == "uml":
        return uml_graph(db, ent)
    if kind == "includes":
        return include_graph(db, ent, depth, False)
    if kind == "includedby":
        return include_graph(db, ent, depth, True)
    if kind == "filedeps":
        return file_dependency_graph(db, ent)
    if kind == "control":
        return control_flow_graph(db, ent)
    if kind == "architecture":
        return architecture_graph(db)
    raise KeyError(kind)
