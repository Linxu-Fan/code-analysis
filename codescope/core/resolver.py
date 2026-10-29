"""Project-wide linking: merges declarations, places members and resolves references."""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict

from . import model as M
from .languages import family

_PARAMS = re.compile(r"\((.*)\)")


def _param_count(sig: str) -> int:
    m = _PARAMS.search(sig or "")
    if not m:
        return -1
    inner = m.group(1).strip()
    if not inner or inner == "void":
        return 0
    depth, n = 0, 1
    for ch in inner:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return n


class _E:
    __slots__ = ("id", "kind", "name", "longname", "lang", "fam", "file_id", "parent", "owner",
                 "modifiers", "type", "signature", "alias")

    def __init__(self, row):
        (self.id, self.kind, self.name, self.longname, self.lang, self.file_id, self.parent,
         self.owner, self.modifiers, self.type, self.signature) = row
        self.fam = family(self.lang)
        self.alias = None

    @property
    def is_decl(self):
        return "decl" in (self.modifiers or "").split()


class Linker:
    def __init__(self, db, progress=None):
        self.db = db
        self.c = db.conn
        self.progress = progress or (lambda *a: None)

    # -------------------------------------------------------------------------------
    def run(self):
        c = self.c
        unresolved = sorted(M.UNRESOLVED_KINDS)
        c.execute("DELETE FROM metrics WHERE entity_id IN (SELECT id FROM entities WHERE kind IN (%s))"
                  % ",".join("?" * len(unresolved)), unresolved)
        c.execute("DELETE FROM entities WHERE kind IN (%s)" % ",".join("?" * len(unresolved)), unresolved)
        c.execute("UPDATE entities SET parent_id=parent0, alias_of=NULL")
        c.execute("UPDATE refs SET dst_id=NULL")
        self.progress("Linking", 0, 1, "Loading entities")
        self.E = {}
        for row in c.execute("SELECT id, kind, name, longname, lang, file_id, parent0, owner, modifiers, type, "
                             "signature FROM entities"):
            e = _E(row)
            self.E[e.id] = e
        self.files = {}
        for fid, relpath, lang, ent in c.execute("SELECT id, relpath, lang, entity_id FROM files"):
            self.files[fid] = (relpath.replace("\\", "/"), lang, ent)
        self.file_ent = {fid: v[2] for fid, v in self.files.items()}

        self._merge_scopes()
        self._place_members()
        self._merge_declarations()
        self._index()
        refs = c.execute("SELECT id, kind, src_id, name, qualifier, file_id FROM refs").fetchall()
        by_kind = defaultdict(list)
        for r in refs:
            by_kind[r[1]].append(r)
        self.result = {}
        self.new_kind = {}
        self.progress("Linking", 0, 1, "Resolving includes and imports")
        self._resolve_includes(by_kind[M.INCLUDE])
        self._resolve_imports(by_kind[M.IMPORT])
        self.progress("Linking", 0, 1, "Resolving inheritance")
        self.bases = defaultdict(list)
        for r in by_kind[M.INHERIT] + by_kind[M.IMPLEMENT]:
            t = self._resolve(r, M.TARGETS[M.INHERIT])
            if t is not None:
                self.result[r[0]] = t
                if t not in self.bases[r[2]]:
                    self.bases[r[2]].append(t)
        rest = [r for k, lst in by_kind.items() if k not in (M.INCLUDE, M.IMPORT, M.INHERIT, M.IMPLEMENT)
                for r in lst]
        n = len(rest)
        for i, r in enumerate(rest):
            if i % 5000 == 0:
                self.progress("Linking", i, n, "Resolving references")
            kinds = M.TARGETS.get(r[1])
            if kinds is None:
                continue
            t = self._resolve(r, kinds)
            if t is not None:
                self.result[r[0]] = t
                if r[1] == M.CALL and self.E[t].kind in M.CLASS_KINDS:
                    self.new_kind[r[0]] = M.CREATE
        self._create_unresolved(refs)
        self._write()

    # ---- structural merging ---------------------------------------------------------------
    def _set_parent(self, eid, pid):
        self.E[eid].parent = pid

    def _merge_scopes(self):
        groups = defaultdict(list)
        for e in self.E.values():
            if e.kind in (M.NAMESPACE, M.PACKAGE, M.MODULE):
                groups[(e.fam, e.kind, e.longname)].append(e.id)
        alias = {}
        for ids in groups.values():
            ids.sort()
            for x in ids[1:]:
                alias[x] = ids[0]
                self.E[x].alias = ids[0]
        if alias:
            for e in self.E.values():
                if e.parent in alias:
                    e.parent = alias[e.parent]

    def _place_members(self):
        by_long = defaultdict(list)
        for e in self.E.values():
            if e.kind in M.CLASS_KINDS and e.alias is None:
                by_long[(e.fam, e.longname)].append(e.id)
        for e in self.E.values():
            if not e.owner:
                continue
            cands = by_long.get((e.fam, e.owner))
            if not cands:
                # owner may be written relative to an enclosing namespace or fully qualified
                tail = "::" + e.owner
                cands = [i for (f, ln), ids in by_long.items() if f == e.fam and ln.endswith(tail) for i in ids]
            if cands:
                cls = self.E[cands[0]]
                e.parent = cls.id
                e.longname = cls.longname + "::" + e.name

    def _merge_declarations(self):
        groups = defaultdict(list)
        for e in self.E.values():
            if e.fam == "c" and (e.kind in M.FUNCTION_KINDS or e.kind == M.VARIABLE) and e.alias is None:
                key = (e.longname, "f" if e.kind in M.FUNCTION_KINDS else "v")
                groups[key].append(e)
        self.mod_updates = {}
        for (_, what), ents in groups.items():
            if len(ents) < 2:
                continue
            defs = [e for e in ents if not e.is_decl]
            decls = [e for e in ents if e.is_decl]
            if not decls:
                continue
            if not defs:
                canon = decls[0]
                decls = decls[1:]
                defs = [canon]
            for d in decls:
                target = defs[0]
                if what == "f" and len(defs) > 1:
                    pc = _param_count(d.signature)
                    for x in defs:
                        if _param_count(x.signature) == pc:
                            target = x
                            break
                d.alias = target.id
                # the in-class declaration knows the class; the definition may not
                if self.E[target.parent].kind not in M.CLASS_KINDS and d.parent and \
                        self.E[d.parent].kind in M.CLASS_KINDS:
                    target.parent = d.parent
                mods = [m for m in (d.modifiers or "").split() if m != "decl"]
                have = (target.modifiers or "").split()
                merged = have + [m for m in mods if m not in have]
                if merged != have:
                    target.modifiers = " ".join(merged)
                    self.mod_updates[target.id] = target.modifiers

    def _index(self):
        self.children = defaultdict(lambda: defaultdict(list))
        self.by_name = defaultdict(list)
        self.by_long = defaultdict(list)
        for e in self.E.values():
            if e.alias is not None:
                continue
            if e.parent is not None:
                self.children[e.parent][e.name].append(e.id)
            if e.kind not in M.LOCAL_KINDS and e.kind != M.FILE:
                self.by_name[e.name].append(e.id)
                self.by_long[e.longname].append(e.id)
        self.rel_to_file = {}
        for fid, (rel, lang, ent) in self.files.items():
            self.rel_to_file[rel] = fid
        self.imports = defaultdict(dict)        # file id -> {bound name: entity id}
        self.import_dotted = defaultdict(dict)  # file id -> {dotted module name: entity id}
        self.star = defaultdict(list)           # file id -> [module file entity ids]
        self.includes = defaultdict(set)        # file id -> {file ids}
        self._closure = {}

    def canonical(self, eid):
        while eid is not None and self.E[eid].alias is not None:
            eid = self.E[eid].alias
        return eid

    # ---- includes / imports ---------------------------------------------------------------
    def _resolve_includes(self, refs):
        names = defaultdict(list)
        for fid, (rel, lang, ent) in self.files.items():
            names[posixpath.basename(rel)].append(fid)
        for rid, kind, src, name, qual, fid in refs:
            name = name.replace("\\", "/")
            base = posixpath.basename(name)
            cands = [f for f in names.get(base, []) if
                     self.files[f][0] == name or self.files[f][0].endswith("/" + name)]
            if not cands:
                continue
            here = posixpath.dirname(self.files[fid][0])
            local = posixpath.normpath(posixpath.join(here, name))
            pick = next((f for f in cands if self.files[f][0] == local), None)
            if pick is None:
                cands.sort(key=lambda f: (self._dir_distance(here, self.files[f][0]), len(self.files[f][0])))
                pick = cands[0]
            if pick != fid:
                self.includes[fid].add(pick)
                self.result[rid] = self.file_ent[pick]

    @staticmethod
    def _dir_distance(a, b):
        pa = [x for x in a.split("/") if x]
        pb = [x for x in posixpath.dirname(b).split("/") if x]
        common = 0
        for x, y in zip(pa, pb):
            if x != y:
                break
            common += 1
        return len(pa) + len(pb) - 2 * common

    def include_closure(self, fid):
        got = self._closure.get(fid)
        if got is not None:
            return got
        seen = set()
        stack = [fid]
        while stack:
            f = stack.pop()
            for g in self.includes.get(f, ()):
                if g not in seen:
                    seen.add(g)
                    stack.append(g)
        self._closure[fid] = seen
        return seen

    def _python_modules(self):
        idx = defaultdict(list)
        for fid, (rel, lang, ent) in self.files.items():
            if lang != "python":
                continue
            p = os.path.splitext(rel)[0].split("/")
            if p and p[-1] == "__init__":
                p = p[:-1]
            for i in range(len(p)):
                idx[".".join(p[i:])].append(fid)
        return idx

    def _module(self, dotted, from_fid):
        if not dotted:
            return None
        rel = self.files[from_fid][0]
        if dotted.startswith("."):
            level = len(dotted) - len(dotted.lstrip("."))
            rest = dotted[level:]
            base = posixpath.dirname(rel)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            path = posixpath.join(base, *rest.split(".")) if rest else base
            path = posixpath.normpath(path) if path else ""
            for cand in (path + ".py", path + "/__init__.py", path + ".pyi"):
                cand = cand.lstrip("./") if cand.startswith("./") else cand
                fid = self.rel_to_file.get(cand)
                if fid is not None:
                    return fid
            return None
        cands = self.pymods.get(dotted)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        here = posixpath.dirname(rel)
        exact = [f for f in cands if self._is_exact_module(f, dotted)]
        pool = exact or cands
        pool.sort(key=lambda f: (self._dir_distance(here, self.files[f][0]), len(self.files[f][0])))
        return pool[0]

    def _is_exact_module(self, fid, dotted):
        p = os.path.splitext(self.files[fid][0])[0].split("/")
        if p and p[-1] == "__init__":
            p = p[:-1]
        return ".".join(p) == dotted

    def _resolve_imports(self, refs):
        self.pymods = self._python_modules()
        for rid, kind, src, name, qual, fid in refs:
            lang = self.files[fid][1]
            target = None
            if lang == "python":
                if name.endswith(".*") or name.endswith("*"):
                    m = self._module(name.rstrip("*").rstrip(".") or name.rstrip("*"), fid)
                    if m is not None:
                        target = self.file_ent[m]
                        self.star[fid].append(target)
                else:
                    m = self._module(name, fid)
                    if m is not None:
                        target = self.file_ent[m]
                    else:
                        mod, _, member = name.rpartition(".")
                        if mod.endswith(".") or mod == "":
                            mod = mod or "."
                        mm = self._module(mod, fid) if mod else None
                        if mm is not None:
                            kids = self.children.get(self.file_ent[mm], {}).get(member)
                            if kids:
                                target = kids[0]
                    if target is not None and qual:
                        plain = "." not in qual and qual == name.split(".")[0] and "." in name
                        if plain:
                            self.import_dotted[fid][name] = target
                        else:
                            self.imports[fid][qual] = target
            else:
                # C++ "using namespace": bind namespace members
                cands = [i for i in self.by_long.get(name.replace(".", "::"), [])
                         if self.E[i].kind == M.NAMESPACE]
                if cands:
                    target = cands[0]
                    self.star[fid].append(target)
            if target is not None:
                self.result[rid] = target

    # ---- reference resolution --------------------------------------------------------------
    def _ok(self, eid, kinds):
        return self.E[eid].kind in kinds

    def _enclosing_class(self, scope):
        s = scope
        while s is not None:
            e = self.E[s]
            if e.kind in M.CLASS_KINDS:
                return s
            s = e.parent
        return None

    def member(self, cls, name, kinds, seen=None):
        """Look up *name* in class *cls* and its base classes."""
        if cls is None:
            return None
        hits = [i for i in self.children.get(cls, {}).get(name, ()) if self._ok(i, kinds)]
        if hits:
            return hits[0]
        if seen is None:
            seen = set()
        seen.add(cls)
        for b in self.bases.get(cls, ()):
            if b in seen or self.E[b].kind not in M.CLASS_KINDS:
                continue
            r = self.member(b, name, kinds, seen)
            if r is not None:
                return r
        return None

    def lexical(self, name, scope, kinds, fid):
        lang = self.E[scope].fam if scope in self.E else ""
        s = scope
        first = True
        while s is not None:
            e = self.E[s]
            if e.kind in M.CLASS_KINDS:
                # Python methods do not see class-level names without "self."
                if lang != "python" or first:
                    r = self.member(s, name, kinds)
                    if r is not None:
                        return r
            else:
                hits = [i for i in self.children.get(s, {}).get(name, ()) if self._ok(i, kinds)]
                if hits:
                    return hits[0]
            if e.kind == M.FILE:
                t = self.imports.get(e.file_id, {}).get(name)
                if t is not None and (self._ok(t, kinds) or self.E[t].kind == M.FILE):
                    return t
                for mod in self.star.get(e.file_id, ()):
                    hits = [i for i in self.children.get(mod, {}).get(name, ()) if self._ok(i, kinds)]
                    if hits:
                        return hits[0]
            first = False
            s = e.parent
        return None

    def global_lookup(self, name, kinds, src, fid, members_only=False):
        fam = self.E[src].fam
        cands = []
        for i in self.by_name.get(name, ()):
            e = self.E[i]
            if e.fam != fam or e.kind not in kinds:
                continue
            p = self.E.get(e.parent) if e.parent is not None else None
            in_class = p is not None and p.kind in M.CLASS_KINDS
            if members_only != in_class:
                if not (members_only is False and in_class and e.kind in M.CLASS_KINDS):
                    continue
            if p is not None and p.kind in M.FUNCTION_KINDS:
                continue
            cands.append(i)
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        here = posixpath.dirname(self.files[fid][0]) if fid in self.files else ""
        inc = self.include_closure(fid) if fam == "c" else set()
        imported = set(self.imports.get(fid, {}).values()) | set(self.star.get(fid, ()))
        scored = []
        for i in cands:
            e = self.E[i]
            s = 0
            if e.file_id == fid:
                s += 4
            if e.file_id in inc:
                s += 3
            if self.file_ent.get(e.file_id) in imported:
                s += 3
            if e.file_id in self.files and posixpath.dirname(self.files[e.file_id][0]) == here:
                s += 1
            if "static" in (e.modifiers or "").split() and e.file_id != fid and e.kind in M.FUNCTION_KINDS | {M.VARIABLE}:
                s -= 5
            scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    def type_of(self, eid, depth=0):
        """Class entity denoting the static type of a variable/function/typedef."""
        if eid is None or depth > 4:
            return None
        e = self.E[eid]
        if e.kind in M.CLASS_KINDS or e.kind in (M.FILE, M.NAMESPACE, M.PACKAGE, M.MODULE):
            return eid
        tname = e.type
        if not tname:
            return None
        if e.fam == "python":
            parts = tname.split(".")
            scope = e.parent if e.parent is not None else eid
            t = self.resolve_chain(parts, scope, e.file_id, depth + 1)
            if t is not None and self.E[t].kind in M.FUNCTION_KINDS | M.CLASS_KINDS:
                return t if self.E[t].kind in M.CLASS_KINDS else None
            return t if t is not None and self.E[t].kind in M.CLASS_KINDS else None
        scope = e.parent if e.parent is not None else eid
        t = self.lexical(tname, scope, M.TYPE_KINDS, e.file_id) or \
            self.global_lookup(tname, M.TYPE_KINDS, eid, e.file_id)
        if t is not None and self.E[t].kind == M.TYPEDEF:
            return self.type_of(t, depth + 1) if self.E[t].type != self.E[t].name else None
        return t

    def resolve_chain(self, parts, scope, fid, depth=0):
        """Resolve a dotted qualifier expression to the entity it denotes."""
        parts = [p for p in parts if p]
        if not parts or depth > 6:
            return None
        cur = None
        start = 1
        dotted = self.import_dotted.get(fid, {})
        if dotted:
            for k in range(len(parts), 0, -1):
                key = ".".join(parts[:k])
                if key in dotted:
                    cur = dotted[key]
                    start = k
                    break
        if cur is None:
            first = parts[0]
            if first in ("self", "this", "cls"):
                cur = self._enclosing_class(scope)
            elif first in ("super", "base"):
                cls = self._enclosing_class(scope)
                cur = self.bases.get(cls, [None])[0] if cls is not None else None
            else:
                any_kinds = M.VARIABLE_KINDS | M.CLASS_KINDS | M.SCOPE_KINDS | M.FUNCTION_KINDS | {M.TYPEDEF, M.FILE}
                cur = self.lexical(first, scope, any_kinds, fid)
                if cur is None:
                    cur = self.global_lookup(first, M.CLASS_KINDS | M.SCOPE_KINDS | {M.TYPEDEF, M.VARIABLE}, scope, fid)
        for p in parts[start:]:
            if cur is None:
                return None
            cur = self.member_of(cur, p, depth)
        return cur

    def member_of(self, container, name, depth=0, kinds=None):
        e = self.E[container]
        if kinds is None:
            kinds = frozenset(M.ALL_KINDS)
        if e.kind in (M.FILE, M.NAMESPACE, M.PACKAGE, M.MODULE):
            hits = [i for i in self.children.get(container, {}).get(name, ()) if self._ok(i, kinds)]
            if hits:
                return hits[0]
            if e.kind == M.FILE:
                t = self.imports.get(e.file_id, {}).get(name)
                if t is not None:
                    return t
            return None
        if e.kind in M.CLASS_KINDS:
            return self.member(container, name, kinds)
        t = self.type_of(container, depth + 1)
        if t is not None and t != container:
            return self.member_of(t, name, depth + 1, kinds)
        return None

    def _resolve(self, r, kinds):
        rid, kind, src, name, qual, fid = r
        if src not in self.E:
            return None
        if kind in (M.INCLUDE, M.IMPORT):
            return None
        if qual:
            q = qual.replace("->", ".").replace("::", ".").replace("()", "").replace("?.", ".")
            q = re.sub(r"\[[^\]]*\]", "", q)
            q = q.strip(".")
            if name in ("__init__", "super") and q in ("super", "base"):
                cls = self._enclosing_class(src)
                for b in self.bases.get(cls, ()):
                    r2 = self.member(b, name, kinds)
                    if r2 is not None:
                        return r2
                return None
            if q in ("super", "base"):
                cls = self._enclosing_class(src)
                for b in self.bases.get(cls, ()):
                    r2 = self.member(b, name, kinds)
                    if r2 is not None:
                        return r2
                return None
            if not re.fullmatch(r"[A-Za-z_][\w.]*", q):
                return self.global_lookup(name, kinds, src, fid, members_only=True)
            container = self.resolve_chain(q.split("."), src, fid)
            if container is not None:
                t = self.member_of(container, name, kinds=kinds)
                if t is not None:
                    return t
                ce = self.E[container]
                if ce.kind in M.CLASS_KINDS or ce.kind in (M.FILE, M.NAMESPACE):
                    return None
            if self.E[src].fam == "c":
                # namespace qualified names: ns::func
                full = qual.replace(".", "::") + "::" + name
                hits = [i for i in self.by_long.get(full, ()) if self._ok(i, kinds)]
                if not hits:
                    tail = "::" + full
                    hits = [i for i in self.by_name.get(name, ())
                            if self._ok(i, kinds) and self.E[i].longname.endswith(tail)]
                if hits:
                    return hits[0]
            return self.global_lookup(name, kinds, src, fid, members_only=True)
        t = self.lexical(name, src, kinds, fid)
        if t is not None:
            return t
        return self.global_lookup(name, kinds, src, fid)

    # ---- unresolved placeholders and persistence -------------------------------------------------------
    def _create_unresolved(self, refs):
        placeholders = {}
        rows = []
        next_id = (max(self.E) if self.E else 0) + 1
        for rid, kind, src, name, qual, fid in refs:
            if rid in self.result:
                continue
            ukind = M.UNRESOLVED_FOR.get(kind)
            if ukind is None or src not in self.E:
                continue
            lang = self.E[src].lang
            fam = self.E[src].fam
            if kind == M.IMPORT:
                ln = name.rstrip("*").rstrip(".")
                nm = ln.rsplit(".", 1)[-1] if fam == "python" else ln
            elif kind == M.INCLUDE:
                ln = nm = name
            else:
                q = qual or ""
                if q in ("self", "this", "cls", "super()", "super", "base") or not re.fullmatch(r"[\w.:]*", q):
                    q = ""
                elif q:
                    head = self.resolve_chain(q.replace("::", ".").split("."), src, fid)
                    if head is not None and self.E[head].kind in M.VARIABLE_KINDS | M.FUNCTION_KINDS:
                        q = ""
                sep = "::" if fam == "c" else "."
                ln = (q.replace("::", sep).replace(".", sep) + sep + name) if q else name
                nm = name
            key = (ukind, fam, ln)
            uid = placeholders.get(key)
            if uid is None:
                uid = next_id
                next_id += 1
                placeholders[key] = uid
                rows.append((uid, ukind, nm, ln, lang))
            self.result[rid] = uid
        self.c.executemany("INSERT INTO entities(id, kind, name, longname, lang, line, col, end_line, end_col, "
                           "signature, modifiers, type, owner) VALUES(?,?,?,?,?,0,0,0,0,'','','','')", rows)

    def _write(self):
        c = self.c
        c.executemany("UPDATE entities SET alias_of=? WHERE id=?",
                      [(e.alias, e.id) for e in self.E.values() if e.alias is not None])
        c.executemany("UPDATE entities SET parent_id=?, longname=? WHERE id=?",
                      [(e.parent, e.longname, e.id) for e in self.E.values()])
        c.executemany("UPDATE entities SET modifiers=? WHERE id=?",
                      [(v, k) for k, v in self.mod_updates.items()])
        upd = []
        for rid, dst in self.result.items():
            dst = self.canonical(dst) if dst in self.E else dst
            upd.append((dst, self.new_kind.get(rid), rid))
        c.executemany("UPDATE refs SET dst_id=?, kind=COALESCE(?, kind) WHERE id=?", upd)
        c.commit()
        self.db.invalidate()


def link(db, progress=None):
    Linker(db, progress).run()
