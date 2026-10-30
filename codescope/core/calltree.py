"""Call trees: callers (ancestors) and/or callees (descendants) of a function as a real tree.

Unlike the call *graph*, every call path gets its own branch, so the tree reads like an
outline of the program flow.  Children are ordered as the calls appear in the source.
Recursion and repeated subtrees are marked instead of being expanded again, which keeps
trees of real code finite and readable.
"""

from __future__ import annotations

import re
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
    members: list = field(default_factory=list)   # class trees: UML style member lines
    rel: str = ""                  # class trees: "inherit"/"compose"; modification trees: "write"
    via: str = ""                  # composition: field name(s) through which the type is held
    writes: list = field(default_factory=list)    # modification trees: [(field name, line)] of a write step
    step: int = 0                  # modification trees: order of the write step (1, 2, ...)
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
    kind: str = "call"                           # "call", "class" or "modify"
    relation: str = "both"                       # class trees: "inherit", "compose" or "both"
    writers: int = 0                             # modification trees: functions that write the target

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


# ---- class (inheritance) trees -------------------------------------------------------------------

INHERIT_KINDS = (M.INHERIT, M.IMPLEMENT)
_VIS = {"public": "+", "private": "-", "protected": "#"}


def _declared_type(m):
    """Type text of a C/C++ member from its signature, without std:: ('vector<Index>')."""
    sig = (m.signature or "").split("=")[0].split("{")[0]
    i = sig.rfind(m.name)
    text = (sig[:i] if i > 0 else sig).replace("std::", "")
    text = re.sub(r"\b(static|mutable|inline|constexpr|const)\b", "", text)
    return " ".join(text.split()) or None


def _member_text(m, mult="", type_name=None):
    """UML style line for a member: '+ name : type', '- run() : int {virtual}'."""
    mods = m.modifiers.split()
    mark = next((_VIS[x] for x in mods if x in _VIS), "-" if m.name.startswith("_") else "+")
    static = " {static}" if "static" in mods else ""
    if m.kind in M.FUNCTION_KINDS:
        ret = f" : {m.type}" if m.type and m.type != "void" and m.kind != M.CONSTRUCTOR else ""
        virt = " {virtual}" if {"virtual", "pure"} & set(mods) else ""
        return f"{mark} {m.name}(){ret}{virt}{static}"
    t = type_name or m.type
    return f"{mark} {m.name}{mult}" + (f" : {t}" if t else "") + static


def _member_lines(db, cls, limit=8):
    fields, methods = [], []
    for m in db.children(cls.id):
        if m.kind in (M.FIELD, M.PROPERTY, M.ENUMERATOR):
            fields.append(_member_text(m))
        elif m.kind in M.FUNCTION_KINDS:
            methods.append(_member_text(m))
    out = fields[:limit] + ([f"  \u2026 {len(fields) - limit} more fields"] if len(fields) > limit else [])
    out += methods[:limit] + ([f"  \u2026 {len(methods) - limit} more methods"] if len(methods) > limit else [])
    return out or ["(no members)"]


def _bases(db, eid, hide_unresolved):
    out, seen = [], set()
    for r in db.refs_from(eid, INHERIT_KINDS):
        e = db.entity(r.dst_id)
        if e is None or e.id in seen or (hide_unresolved and e.is_unresolved):
            continue
        seen.add(e.id)
        out.append((e, r.line, r.file_id))
    return out


def _derived(db, eid):
    out, seen = [], set()
    for r in db.refs_to(eid, INHERIT_KINDS):
        e = db.entity(r.src_id)
        if e is None or e.id in seen or e.kind not in M.CLASS_KINDS:
            continue
        seen.add(e.id)
        out.append((e, r.line, r.file_id))
    out.sort(key=lambda t: t[0].name.lower())
    return out


# Wrapper templates are unwrapped to the type they hold; the field is marked with a multiplicity.
_CONTAINERS = M.CONTAINER_TYPES
_POINTERS = M.POINTER_TYPES
_OTHER_WRAPPERS = frozenset({"optional", "Optional", "variant", "pair", "atomic", "reference_wrapper",
                             "basic_string", "string", "wstring", "string_view", "function"})
_WRAPPERS = _CONTAINERS | _POINTERS | _OTHER_WRAPPERS
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _multiplicity(sig: str) -> str:
    words = set(_IDENT.findall(sig.split("=")[0]))
    if words & _CONTAINERS or "[" in sig.split("=")[0]:
        return "[]"
    if words & _POINTERS or "*" in sig.split("=")[0]:
        return "*"
    if "optional" in words or "Optional" in words:
        return "?"
    return ""


def _alias_target(alias) -> str:
    """Name an alias/typedef really refers to: 'using A = typename X<T>::Y' -> 'Y', 'using B = Foo<T>' -> 'Foo'."""
    sig = alias.signature or ""
    if "=" in sig:
        rhs = sig.split("=", 1)[1]
    else:
        rhs = sig.replace("typedef", " ", 1).rsplit(alias.name, 1)[0]
    prev = None
    while prev != rhs:                      # drop template arguments
        prev = rhs
        rhs = re.sub(r"<[^<>]*>", "", rhs)
    words = [w for w in _IDENT.findall(rhs.split("::")[-1]) if w not in ("typename", "const", "struct", "class")]
    return words[-1] if words else ""


def _class_by_name(db, name, lang_family, cache, _seen=None):
    """A project class/struct called *name* (aliases followed to what they name), or None."""
    key = (name, lang_family)
    if key in cache:
        return cache[key]
    from .languages import family
    seen = (_seen or set()) | {name}
    hit = None
    for e in db.entities(name=name, kinds=sorted(M.CLASS_KINDS | {M.TYPEDEF})):
        if family(e.lang) != lang_family or e.file_id is None:
            continue
        if e.kind == M.TYPEDEF:
            target = _alias_target(e)
            e = _class_by_name(db, target, lang_family, cache, seen) if target and target not in seen else None
        if e is not None:
            hit = e
            break
    cache[key] = hit
    return hit


def _field_types(db, f, hide_unresolved, cache):
    """Types a field holds (wrappers such as vector or shared_ptr unwrapped): [entity]."""
    from .languages import family
    fam = family(f.lang)
    targets = []
    for r in db.refs_from(f.id, [M.TYPED]):
        e = db.entity(r.dst_id)
        if e is None or e.name in _WRAPPERS:
            continue
        if e.kind == M.TYPEDEF:
            e = _class_by_name(db, e.name, fam, cache) or e
        # aliases of non-project types (e.g. Eigen vectors) count as external types
        external = e.kind in M.UNRESOLVED_KINDS or e.kind == M.TYPEDEF
        if (e.kind in M.CLASS_KINDS or (external and not hide_unresolved)) and e not in targets:
            targets.append(e)
    if not targets:
        # older databases or dynamic languages: use the declared type text
        text = (f.signature or f.type or "")
        if f.signature and f.name in text:
            text = text[: text.rfind(f.name)]
        for word in _IDENT.findall(text):
            if word in _WRAPPERS or word == f.name:
                continue
            e = _class_by_name(db, word, fam, cache)
            if e is not None and e not in targets:
                targets.append(e)
    return targets


def _parts(db, cls, hide_unresolved, cache):
    """Types held by the fields of *cls*, in declaration order: [(entity, line, file, via)]."""
    order, info = [], {}
    for f in db.children(cls.id, [M.FIELD, M.PROPERTY]):
        if "static" in f.modifiers.split():
            continue
        targets = _field_types(db, f, hide_unresolved, cache)
        mult = _multiplicity(f.signature or "")
        for e in targets:
            if e.id == cls.id and not mult:
                continue
            label = f.name + mult
            if e.id not in info:
                order.append(e.id)
                info[e.id] = [e, f.line, f.file_id, [label]]
            elif label not in info[e.id][3]:
                info[e.id][3].append(label)
    return [(info[i][0], info[i][1], info[i][2], ", ".join(info[i][3])) for i in order]


def _holding_fields(db, cls):
    """Fields of other classes whose declared type involves *cls*: [(field, owner class)]."""
    names = {cls.name}
    targets = [cls.id]
    for t in db.entities(kinds=[M.TYPEDEF]):
        if t.type == cls.name:
            targets.append(t.id)
            names.add(t.name)
    out, seen = [], set()
    for tid in targets:
        for r in db.refs_to(tid, [M.TYPED]):
            f = db.entity(r.src_id)
            if f is None or f.kind not in (M.FIELD, M.PROPERTY) or f.id in seen:
                continue
            owner = db.parent(f)
            if owner is None or owner.kind not in M.CLASS_KINDS or owner.id == cls.id:
                continue
            seen.add(f.id)
            out.append((f, owner))
    if not out and cls.lang == "python":
        for f in db.entities(kinds=[M.FIELD]):
            if f.type and f.type.split(".")[-1] in names:
                owner = db.parent(f)
                if owner is not None and owner.kind in M.CLASS_KINDS and owner.id != cls.id:
                    out.append((f, owner))
    return out


def _owners(db, cls):
    """Classes that hold *cls* in one of their fields: [(owner entity, line, file, via)]."""
    out = {}
    for f, owner in _holding_fields(db, cls):
        label = f.name + (_multiplicity(f.signature or "") if f.lang != "python" else "")
        entry = out.setdefault(owner.id, [owner, f.line, f.file_id, []])
        if label not in entry[3]:
            entry[3].append(label)
    return sorted(((o, l, fi, ", ".join(v)) for o, l, fi, v in out.values()), key=lambda t: t[0].name.lower())


def build_class_tree(db, entity, descendants=True, ancestors=True, depth=6, hide_unresolved=False,
                     members=False, max_nodes=1500, relation="both"):
    """Structure tree of a class/struct.

    *relation* "inherit": base classes (ancestors) and derived classes (descendants).
    *relation* "compose": classes that contain it as a member (ancestors) and the types of its
    own fields, recursively (descendants).  "both" combines the two.
    """
    root = TreeNode(entity, 0)
    tree = CallTree(root, kind="class", relation=relation)
    budget = [max_nodes]
    cache = {}
    inherit = relation in ("inherit", "both")
    compose = relation in ("compose", "both")

    def related(e, side):
        out = []
        if side == DOWN:
            if compose:
                out += [(x, l, f, "compose", via) for x, l, f, via in _parts(db, e, hide_unresolved, cache)]
            if inherit:
                out += [(x, l, f, "inherit", "") for x, l, f in _derived(db, e.id)]
        else:
            if inherit:
                out += [(x, l, f, "inherit", "") for x, l, f in _bases(db, e.id, hide_unresolved)]
            if compose:
                out += [(x, l, f, "compose", via) for x, l, f, via in _owners(db, e)]
        return out

    def expand(node, path, side, level, expanded):
        items = related(node.entity, side)
        if level >= depth:
            node.truncated = bool(items)
            return
        for e, line, fid, rel, via in items:
            if budget[0] <= 0:
                node.truncated = tree.truncated_nodes = True
                return
            budget[0] -= 1
            child = TreeNode(e, level + 1 if side == DOWN else -(level + 1), side, call_line=line, call_file=fid,
                             rel=rel, via=via)
            node.children.append(child)
            if e.id in path:
                child.recursive = True
                continue
            if e.is_unresolved:
                continue
            if e.id in expanded:            # shared parts / diamond inheritance: expanded once
                child.repeated = bool(related(e, side))
                continue
            expanded.add(e.id)
            expand(child, path | {e.id}, side, level + 1, expanded)

    if descendants:
        expand(root, {entity.id}, DOWN, 0, {entity.id})
        tree.down, root.children = root.children, []
    if ancestors:
        expand(root, {entity.id}, UP, 0, {entity.id})
        tree.up, root.children = root.children, []
    if members:
        for n in tree.nodes():
            if not n.entity.is_unresolved:
                n.members = _member_lines(db, n.entity)
    return tree


# ---- member trees --------------------------------------------------------------------------------

MEMBER_KINDS = [M.FIELD, M.PROPERTY, M.METHOD, M.CONSTRUCTOR, M.FUNCTION]


def build_member_tree(db, entity, descendants=True, ancestors=True, depth=3, hide_unresolved=True,
                      max_nodes=1500):
    """All members of a class/struct as a tree.

    Descendants: every data member and member function in declaration order.  A data member
    whose type is a project class (also inside vector, shared_ptr, optional, ...) expands into
    the members of that class, level by level.  Ancestors: only the direct base classes and the
    classes that directly hold this one in a field.
    """
    root = TreeNode(entity, 0)
    tree = CallTree(root, kind="class", relation="members")
    budget = [max_nodes]
    cache = {}

    def members(cls):
        return db.children(cls.id, MEMBER_KINDS)

    def expand(node, cls, path, level, expanded):
        items = members(cls)
        if level >= depth:
            node.truncated = bool(items)
            return
        for m in items:
            if budget[0] <= 0:
                node.truncated = tree.truncated_nodes = True
                return
            budget[0] -= 1
            is_field = m.kind in (M.FIELD, M.PROPERTY)
            mult = _multiplicity(m.signature or "") if is_field else ""
            types = _field_types(db, m, True, cache) if is_field else []
            # show what a wrapper holds: links[] : Link rather than links[] : vector
            shown = None
            if is_field and m.type in _WRAPPERS:
                shown = ", ".join(t.name for t in types) if types else _declared_type(m)
            child = TreeNode(m, level + 1, DOWN, call_line=m.line, call_file=m.file_id, rel="member",
                             via=_member_text(m, mult, shown))
            node.children.append(child)
            for t in types:
                if t.id in path:
                    child.recursive = True
                elif t.id in expanded:            # the same type was expanded elsewhere
                    child.repeated = bool(members(t))
                else:
                    expanded.add(t.id)
                    expand(child, t, path | {t.id}, level + 1, expanded)

    if descendants:
        expand(root, entity, {entity.id}, 0, {entity.id})
        tree.down, root.children = root.children, []
    if ancestors:
        up = [(e, l, f, "inherit", "") for e, l, f in _bases(db, entity.id, hide_unresolved)]
        up += [(e, l, f, "compose", via) for e, l, f, via in _owners(db, entity)]
        tree.up = [TreeNode(e, -1, UP, call_line=l, call_file=f, rel=rel, via=via) for e, l, f, rel, via in up]
    return tree


# ---- modification trees ------------------------------------------------------------------------

SCOPE_NODE_KINDS = M.FUNCTION_KINDS | {M.FILE}       # module level code (Python scripts) can call too


def modifiable_fields(db, cls):
    """Fields of *cls*, including inherited ones, in declaration order."""
    out, seen, stack = [], set(), [cls]
    while stack:
        c = stack.pop(0)
        if c.id in seen:
            continue
        seen.add(c.id)
        out += db.children(c.id, [M.FIELD, M.PROPERTY])
        stack += [b for b, _, _ in _bases(db, c.id, True)]
    return out


def _is_ctor(f):
    return f.kind == M.CONSTRUCTOR or (f.kind == M.METHOD and f.name == "__init__")


def _callers_of(db, f):
    """Entities whose code calls *f*; constructors are also reached through their class."""
    refs = list(db.refs_to(f.id, CALL_KINDS))
    if _is_ctor(f):
        cls = db.parent(f)
        if cls is not None and cls.kind in M.CLASS_KINDS:
            refs += db.refs_to(cls.id, CALL_KINDS)
    out = []
    for r in refs:
        e = db.entity(r.src_id)
        if e is not None and e.kind in SCOPE_NODE_KINDS and e.id not in out:
            out.append(e.id)
    return out


def _line_list(lines):
    """'line 5' / 'lines 5, 6, 9 +2'."""
    nums = ", ".join(map(str, lines[:3])) + (f" +{len(lines) - 3}" if len(lines) > 3 else "")
    return f"line{'s' if len(lines) > 1 else ''} {nums}"


def _write_lines(writes, limit=12):
    """Card lines of a write step: one per member with its line numbers."""
    by_name = {}
    for name, line, whole in writes:
        by_name.setdefault(f"= {name}" if whole else name, []).append(line)
    w = max(len(name) for name in by_name)
    out = [f"{name:<{w}}  {_line_list(lines)}" for name, lines in by_name.items()]
    if len(out) > limit:
        out = out[:limit - 1] + [f"\u2026 {len(out) - limit + 1} more members"]
    return out


def holder_paths(db, cls, max_depth=4):
    """Fields that hold *cls* by value, directly or nested: [(field, path)].

    For ``struct World { Options options; }; struct Options { Settings settings; };`` and
    *cls* = Settings this gives (Options::settings, "settings") and
    (World::options, "options.settings").  Containers and pointers are left out: adding to a
    vector or re-pointing a pointer does not change an existing object.
    """
    out, frontier, seen = [], [(cls, "")], {cls.id}
    for _ in range(max_depth):
        nxt = []
        for c, path in frontier:
            for f, owner in _holding_fields(db, c):
                if "static" in f.modifiers.split() or _multiplicity(f.signature or "") not in ("", "?"):
                    continue
                p = f.name + ("." + path if path else "")
                out.append((f, p))
                if owner.id not in seen:
                    seen.add(owner.id)
                    nxt.append((owner, p))
        frontier = nxt
    return out


def _last_part(qualifier):
    q = re.sub(r"\[[^\]]*\]|\(\)", "", qualifier or "").replace("->", ".").replace("::", ".")
    return q.rstrip(".").rsplit(".", 1)[-1]


def build_modify_tree(db, entity, member=None, depth=10, paths=True, max_nodes=2000, whole=True):
    """Where and in which order a class/struct (or one of its members) is modified.

    Every function that writes a field of *entity* (inherited fields included), or only
    *member* when given, is found.  With *paths* the tree starts at the entry points (functions
    nobody calls, e.g. ``main``) and follows only the calls that lead to a write.  A function's
    children are its calls and writes in source order, so a depth-first reading of the tree is
    the order in which the modifications happen; write steps are numbered #1, #2, ...

    With *whole*, assigning a field that holds the class by value (``world.options = other``,
    nested holders included) also counts: it replaces every member at once.  A write to a
    sub-member of such a field (``world.options.time_step = x``) does not.
    """
    root = TreeNode(entity, 0)
    if member is not None:
        root.rel, root.via = "target", member.name
    tree = CallTree(root, kind="modify")
    fields = [member] if member is not None else modifiable_fields(db, entity)
    writes = {}                     # function id -> [(line, col, field, file, label, whole)]

    def add(r, f, label, is_whole):
        src = db.entity(r.src_id)
        if src is None or src.kind not in SCOPE_NODE_KINDS:
            return
        lst = writes.setdefault(src.id, [])
        if not any(w[0] == r.line and w[2].id == f.id for w in lst):
            lst.append((r.line, r.col, f, r.file_id, label, is_whole))

    for f in fields:
        for r in db.refs_to(f.id, [M.SET]):
            add(r, f, f.name, False)
    if whole:
        line_refs = {}
        for f, path in holder_paths(db, entity):
            for r in db.refs_to(f.id, [M.SET]):
                key = (r.src_id, r.line)
                if key not in line_refs:
                    line_refs[key] = [x for x in db.refs_from(r.src_id, [M.SET, M.CALL, M.USE], resolved=False)
                                      if x.line == r.line]
                # x.f.y = v / x.f.g() only touch part of f: not a whole replacement
                if any(x.col > r.col and _last_part(x.qualifier) == f.name for x in line_refs[key]):
                    continue
                add(r, f, path, True)
    tree.writers = len(writes)
    if not writes:
        return tree
    # every function from which a write can be reached
    reach, stack, callers = set(writes), list(writes), {}
    while stack:
        fid = stack.pop()
        callers[fid] = _callers_of(db, db.entity(fid))
        for c in callers[fid]:
            if c not in reach:
                reach.add(c)
                stack.append(c)
    ctors = {}

    def call_targets(r):
        e = db.entity(r.dst_id)
        if e is None:
            return []
        if e.id in reach:
            return [e]
        if e.kind in M.CLASS_KINDS:              # Foo() / new Foo -> its constructors
            if e.id not in ctors:
                ctors[e.id] = [c for c in db.children(e.id, sorted(M.FUNCTION_KINDS)) if _is_ctor(c)]
            return [c for c in ctors[e.id] if c.id in reach]
        return []

    def steps(fid, with_calls):
        items = [(line, col, "w", (f, label, is_whole), file)
                 for line, col, f, file, label, is_whole in writes.get(fid, ())]
        if with_calls:
            seen = set()
            for r in db.refs_from(fid, CALL_KINDS):
                for e in call_targets(r):
                    if e.id not in seen:
                        seen.add(e.id)
                        items.append((r.line, r.col, "c", e, r.file_id))
        items.sort(key=lambda t: (t[0], t[1]))
        return items

    budget = [max_nodes]

    def expand(node, path, level, expanded):
        items = steps(node.entity.id, paths)
        if level >= depth:
            node.truncated = bool(items)
            return
        group = None
        for line, _col, what, e, file in items:
            if what == "w":
                e, label, is_whole = e
                if group is not None:
                    group.writes.append((label, line, is_whole))
                    continue
            if budget[0] <= 0:
                node.truncated = tree.truncated_nodes = True
                return
            budget[0] -= 1
            child = TreeNode(e, level + 1, DOWN, call_line=line, call_file=file)
            node.children.append(child)
            if what == "w":
                child.rel = "write"
                child.writes.append((label, line, is_whole))
                group = child
                continue
            group = None
            if e.id in path:
                child.recursive = True
                continue
            if e.id in expanded:
                child.repeated = True
                continue
            expanded.add(e.id)
            expand(child, path | {e.id}, level + 1, expanded)

    def sort_key(e):
        f = db.file(e.file_id) if e.file_id else None
        return (e.name not in ("main", "WinMain", "wmain"), f.relpath if f else "", e.line)

    if paths:
        starts = sorted((db.entity(i) for i in reach if not callers.get(i)), key=sort_key)
    else:
        starts = sorted((db.entity(i) for i in writes), key=sort_key)
    expanded, pending = set(), sorted((db.entity(i) for i in writes), key=sort_key)
    while True:
        for e in starts:
            if e.id in expanded:
                continue
            expanded.add(e.id)
            node = TreeNode(e, 1, DOWN)
            tree.down.append(node)
            expand(node, {e.id}, 1, expanded)
        # writers only reachable through a call cycle get a root of their own
        shown = {n.entity.id for c in tree.down for n in c.walk()}
        starts = [e for e in pending if e.id not in shown][:1]
        if not starts or budget[0] <= 0:
            break
    k = 0
    for n in tree.nodes():
        if n.rel == "write":
            k += 1
            n.step = k
            if len({(name, w) for name, _, w in n.writes}) > 1:
                n.members = _write_lines(n.writes)
    # a subtree shown once is referenced by its step numbers where it is called again
    first = {n.entity.id: n for n in tree.nodes()
             if n.depth > 0 and n.rel != "write" and not n.repeated and not n.recursive}
    for n in tree.nodes():
        if n.repeated and n.entity.id in first:
            steps = [w.step for w in first[n.entity.id].walk() if w.rel == "write"]
            if steps:
                lo, hi = min(steps), max(steps)
                n.via = f"repeats #{lo}" + (f"\u2013#{hi}" if hi != lo else "")
    return tree


# ---- layout ------------------------------------------------------------------------------------

LINE_H = 15.0


def node_text(n: TreeNode) -> str:
    """Label plus member lines (one per line)."""
    return "\n".join([_display(n)] + list(n.members))


def default_measure(label, char_w=7.0, pad_x=14.0, height=26.0):
    lines = label.split("\n")
    w = max(len(l) for l in lines) * char_w + 2 * pad_x
    h = height + (len(lines) - 1) * LINE_H + (8 if len(lines) > 1 else 0)
    return max(w, 46.0), h


def layout_call_tree(tree: CallTree, measure=default_measure, level_gap=56.0, sibling_gap=8.0, margin=24.0):
    """Tidy left-to-right layout: descendants grow to the right, ancestors to the left.

    Leaves are stacked in order without overlaps and every parent is centred on its
    children, giving a balanced, evenly distributed tree.
    """
    for n in tree.nodes():
        n.width, n.height = measure(node_text(n))

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
    if n.rel == "member":
        label = n.via
        if n.recursive:
            return label + "  ↻"
        if n.repeated:
            return label + "  …"
        return label + ("  ▸" if n.truncated and not n.children else "")
    if n.rel == "write":
        parts = []
        for verb, is_whole in (("sets", False), ("replaces", True)):
            names = list(dict.fromkeys(name for name, _, w in n.writes if w == is_whole))
            if names:
                parts.append(f"{verb} " + ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""))
        text = f"#{n.step}  " + "; ".join(parts)
        if not n.members:
            text += f"  ({_line_list([line for _, line, _ in n.writes])})"
        return text
    label = n.label
    if n.rel == "target":                 # modification tree of one member: Class::member
        label += ("::" if n.entity.lang in ("c", "cpp") else ".") + n.via
    if n.rel == "compose" and n.via:
        label = f"{n.via} : {label}" if n.side == DOWN else f"{label}.{n.via}"
    if n.recursive:
        label += "  ↻"
    elif n.repeated and n.via and n.rel != "compose":
        label += f"  … {n.via}"
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
    "write": ("#ffebe9", "#1f2328", "#cf222e"),
    "field": ("#f4f0fd", "#1f2328", "#8250df"),
}


def node_category(n: TreeNode):
    k = n.entity.kind
    if n.depth == 0:
        return "root"
    if n.rel == "write":
        return "write"
    if k in (M.FIELD, M.PROPERTY):
        return "field"
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
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8b949e"/></marker>'
           '<marker id="tri" viewBox="0 0 12 12" refX="11" refY="6" markerWidth="11" markerHeight="11" '
           'orient="auto-start-reverse"><path d="M0,0 L12,6 L0,12 z" fill="#ffffff" stroke="#8b949e"/></marker>'
           '<marker id="dia" viewBox="0 0 16 10" refX="1" refY="5" markerWidth="14" markerHeight="9" '
           'orient="auto"><path d="M0,5 L8,0 L16,5 L8,10 z" fill="#8b949e"/></marker></defs>',
           '<rect width="100%" height="100%" fill="#ffffff"/>']
    if title:
        out.append(f'<text x="12" y="18" font-size="13" font-weight="600" fill="#1f2328">{escape(title)}</text>')
    out.append(f'<g transform="translate(0,{oy})">')
    for parent, child, side in edges(tree):
        entry = tree.kind == "modify" and parent.depth == 0      # root -> entry point: not a call
        dash = ' stroke-dasharray="4,3"' if child.entity.is_unresolved or entry else ""
        if tree.kind == "class":
            marker = {"compose": 'marker-start="url(#dia)"', "inherit": 'marker-start="url(#tri)"'}.get(child.rel, "")
        else:
            marker = "" if entry else 'marker-end="url(#ah)"'
        out.append(f'<path d="{path_d(connector(parent, child, side))}" fill="none" stroke="#8b949e" '
                   f'stroke-width="1.3"{dash} {marker}/>')
    for n in tree.nodes():
        fill, fg, border = NODE_COLORS[node_category(n)]
        x, y = n.x - n.width / 2, n.y - n.height / 2
        dash = ' stroke-dasharray="4,3"' if (n.entity.is_unresolved or n.repeated) else ""
        where = "declared" if tree.kind == "class" else "written" if n.rel == "write" else "called"
        tip = escape(n.entity.longname + (f"  ({where} at line {n.call_line})" if n.call_line else ""))
        body = (f'<g><title>{tip}</title><rect x="{x:.1f}" y="{y:.1f}" width="{n.width:.1f}" '
                f'height="{n.height:.1f}" rx="6" fill="{fill}" stroke="{border}" stroke-width="1.3"{dash}/>')
        bold = ' font-weight="600"' if n.depth == 0 or n.members else ""
        if n.members:
            ty = y + 17
            body += (f'<text x="{n.x:.1f}" y="{ty:.1f}" text-anchor="middle" fill="{fg}"{bold}>'
                     f'{escape(_display(n))}</text>')
            sep = y + 25
            body += f'<line x1="{x:.1f}" y1="{sep:.1f}" x2="{x + n.width:.1f}" y2="{sep:.1f}" stroke="{border}"/>'
            for i, line in enumerate(n.members):
                body += (f'<text x="{x + 8:.1f}" y="{sep + 13 + i * LINE_H:.1f}" fill="{fg}" font-size="11" '
                         f'font-family="Consolas, Menlo, monospace">{escape(line)}</text>')
        else:
            body += (f'<text x="{n.x:.1f}" y="{n.y + 4:.1f}" text-anchor="middle" fill="{fg}"{bold}>'
                     f'{escape(_display(n))}</text>')
        out.append(body + "</g>")
    out.append("</g></svg>")
    return "\n".join(out)


def to_text(tree: CallTree):
    """Indented text rendering (for the command line)."""
    lines = []

    def walk(nodes, prefix, arrow):
        for i, n in enumerate(nodes):
            last = i == len(nodes) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{arrow}{_display(n)}"
                         + (f"   [line {n.call_line}]" if n.call_line and n.rel != "write"
                            and tree.kind in ("call", "modify") else ""))
            if n.rel == "write" and n.members:
                pad = prefix + ("    " if last else "│   ")
                lines.extend(f"{pad}    {m}" for m in _write_lines(n.writes, limit=10 ** 6))
            walk(n.children, prefix + ("    " if last else "│   "), arrow)
    if tree.kind == "class":
        up_title, down_title = {
            "inherit": ("Base classes (ancestors):", "Derived classes (descendants):"),
            "compose": ("Contained in (ancestors):", "Member types (descendants):"),
            "members": ("Direct base classes and containers (ancestors):", "Members (descendants):"),
        }.get(tree.relation, ("Base classes and containers (ancestors):",
                              "Member types and derived classes (descendants):"))
    elif tree.kind == "modify":
        up_title, down_title = "", "Modified in (call paths, in execution order):"
    else:
        up_title, down_title = ("Callers (ancestors):", "Callees (descendants):")
    if tree.up:
        lines.append(up_title)
        lines.append(f"{tree.root.label}")
        walk(tree.up, "", "← ")
    if tree.down:
        if tree.up:
            lines.append("")
        lines.append(down_title)
        lines.append(_display(tree.root))
        walk(tree.down, "", "")
    if not tree.up and not tree.down:
        what = {"class": "related classes", "modify": "modifications"}.get(tree.kind, "calls")
        lines.append(f"{_display(tree.root)}   (no {what} found)")
    lines.append("")
    if tree.kind == "class":
        lines.append("… already shown elsewhere   ↻ cycle   ▸ depth limit   "
                     "name[] container, name* pointer, name? optional member")
    elif tree.kind == "modify":
        lines.append("#n order of the modifications   = / replaces: whole object assigned   ↻ recursion   … expanded elsewhere   ▸ depth limit reached")
    else:
        lines.append("↻ recursion   … repeated subtree (shown once)   ▸ depth limit reached")
    return "\n".join(lines)
