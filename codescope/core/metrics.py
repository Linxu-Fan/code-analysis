"""Metrics computed after linking (coupling, inheritance, cohesion, fan-in/out)."""

from __future__ import annotations

from collections import defaultdict

from . import model as M

METRIC_DESCRIPTIONS = {
    "CountLine": "Number of physical lines",
    "CountLineCode": "Lines containing code",
    "CountLineComment": "Lines containing comments",
    "CountLineBlank": "Blank lines",
    "RatioCommentToCode": "Comment lines / code lines",
    "Cyclomatic": "McCabe cyclomatic complexity",
    "MaxNesting": "Maximum nesting depth of control structures",
    "CountStmt": "Number of statements",
    "CountReturn": "Number of return statements",
    "CountParams": "Number of parameters",
    "CountInput": "Fan-in: distinct callers",
    "CountOutput": "Fan-out: distinct callees",
    "HalsteadVolume": "Halstead program volume",
    "HalsteadDifficulty": "Halstead difficulty",
    "HalsteadEffort": "Halstead effort",
    "MaintainabilityIndex": "Maintainability index (0-100, higher is better)",
    "CountDeclFunction": "Functions declared in file",
    "CountDeclClass": "Classes declared in file",
    "SumCyclomatic": "Sum of cyclomatic complexity (WMC for classes)",
    "MaxCyclomatic": "Maximum cyclomatic complexity",
    "AvgCyclomatic": "Average cyclomatic complexity",
    "CountDeclMethod": "Methods declared in class",
    "CountDeclMethodPublic": "Public methods declared in class",
    "CountDeclInstanceVariable": "Fields declared in class",
    "CountClassBase": "Immediate base classes",
    "CountClassDerived": "Immediate subclasses (NOC)",
    "MaxInheritanceTree": "Depth of inheritance tree (DIT)",
    "CountClassCoupled": "Coupling between objects (CBO)",
    "PercentLackOfCohesion": "Lack of cohesion in methods (LCOM %)",
}


def compute_derived(db):
    c = db.conn
    q = c.execute
    ents = {}
    for eid, kind, parent, mods, alias in q("SELECT id, kind, parent_id, modifiers, alias_of FROM entities"):
        ents[eid] = (kind, parent, mods or "", alias)
    rows = []

    # fan-in / fan-out
    fan_in = defaultdict(set)
    fan_out = defaultdict(set)
    for src, dst in q("SELECT src_id, dst_id FROM refs WHERE kind IN (?, ?) AND dst_id IS NOT NULL",
                      (M.CALL, M.CREATE)):
        if src != dst:
            fan_out[src].add(dst)
            fan_in[dst].add(src)
    for eid, (kind, parent, mods, alias) in ents.items():
        if kind in M.FUNCTION_KINDS and alias is None:
            rows.append((eid, "CountInput", len(fan_in.get(eid, ()))))
            rows.append((eid, "CountOutput", len(fan_out.get(eid, ()))))

    # class structure
    children = defaultdict(list)
    for eid, (kind, parent, mods, alias) in ents.items():
        if parent is not None and alias is None:
            children[parent].append(eid)
    bases = defaultdict(set)
    derived = defaultdict(set)
    for src, dst in q("SELECT src_id, dst_id FROM refs WHERE kind IN (?, ?) AND dst_id IS NOT NULL",
                      (M.INHERIT, M.IMPLEMENT)):
        bases[src].add(dst)
        derived[dst].add(src)

    def depth(cid, seen=()):
        bs = [b for b in bases.get(cid, ()) if b not in seen and ents.get(b, ("",))[0] in M.CLASS_KINDS]
        if not bs:
            return 0
        return 1 + max(depth(b, seen + (cid,)) for b in bs)

    def owner_class(eid):
        cur = eid
        for _ in range(20):
            info = ents.get(cur)
            if info is None:
                return None
            if info[0] in M.CLASS_KINDS or info[0] in M.UNRESOLVED_KINDS:
                return cur
            cur = info[1]
            if cur is None:
                return None
        return None

    cyclo = {eid: v for eid, v in q("SELECT entity_id, value FROM metrics WHERE name='Cyclomatic'")}
    # references from members of a class
    member_refs = defaultdict(list)     # class id -> [(member id, kind, dst)]
    for src, kind, dst in q("SELECT src_id, kind, dst_id FROM refs WHERE dst_id IS NOT NULL"):
        oc = owner_class(src)
        if oc is not None:
            member_refs[oc].append((src, kind, dst))

    for cid, (kind, parent, mods, alias) in ents.items():
        if kind not in M.CLASS_KINDS or alias is not None:
            continue
        kids = children.get(cid, [])
        methods = [k for k in kids if ents[k][0] in M.FUNCTION_KINDS]
        fields = [k for k in kids if ents[k][0] in (M.FIELD, M.PROPERTY)]
        public = [k for k in methods if "public" in ents[k][2].split() or
                  ("private" not in ents[k][2].split() and "protected" not in ents[k][2].split())]
        rows += [
            (cid, "CountDeclMethod", len(methods)),
            (cid, "CountDeclMethodPublic", len(public)),
            (cid, "CountDeclInstanceVariable", len(fields)),
            (cid, "CountClassBase", len(bases.get(cid, ()))),
            (cid, "CountClassDerived", len(derived.get(cid, ()))),
            (cid, "MaxInheritanceTree", depth(cid)),
            (cid, "SumCyclomatic", sum(cyclo.get(m, 1) for m in methods)),
            (cid, "MaxCyclomatic", max((cyclo.get(m, 1) for m in methods), default=0)),
        ]
        coupled = set()
        used_by = defaultdict(set)     # field -> methods using it
        fset = set(fields)
        for src, rk, dst in member_refs.get(cid, ()):
            oc = owner_class(dst)
            if oc is not None and oc != cid:
                coupled.add(oc)
            if dst in fset and src in methods:
                used_by[dst].add(src)
        coupled -= bases.get(cid, set()) and set()
        rows.append((cid, "CountClassCoupled", len(coupled)))
        if methods and fields:
            avg = sum(len(used_by.get(f, ())) for f in fields) / len(fields)
            lcom = round(100.0 * (1.0 - avg / len(methods)))
        else:
            lcom = 0
        rows.append((cid, "PercentLackOfCohesion", max(0, lcom)))
    names = ["CountInput", "CountOutput", "CountDeclMethod", "CountDeclMethodPublic", "CountDeclInstanceVariable",
             "CountClassBase", "CountClassDerived", "MaxInheritanceTree", "CountClassCoupled",
             "PercentLackOfCohesion"]
    c.execute("DELETE FROM metrics WHERE name IN (%s)" % ",".join("?" * len(names)), names)
    c.execute("DELETE FROM metrics WHERE name IN ('SumCyclomatic','MaxCyclomatic') AND entity_id IN "
              "(SELECT id FROM entities WHERE kind IN (%s))" % ",".join("?" * len(M.CLASS_KINDS)),
              sorted(M.CLASS_KINDS))
    c.executemany("INSERT OR REPLACE INTO metrics(entity_id, name, value) VALUES(?,?,?)", rows)
    c.commit()
