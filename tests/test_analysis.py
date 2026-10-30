import os

from codescope.core import model as M
from codescope.core.analyzer import Analyzer
from codescope.core.cfg import build_cfg
from codescope.core.graphs import build_graph, graphs_for
from codescope.core.layout import layout
from codescope.core.svg import to_dot, to_svg

from .conftest import one


def related(db, e, kind, reverse=False):
    return {x.longname for x in db.related(e.id, (kind,), reverse=reverse)}


def test_cpp_resolution(cpp_db):
    db = cpp_db
    main = one(db, "main")
    calls = related(db, main, M.CALL)
    assert {"geo::Shape::area", "geo::Shape::describe", "geo::Rect::isSquare", "classify", "clamp",
            "log_message", "geo::Shape::count"} <= calls
    assert {"geo::Circle", "geo::Rect"} <= related(db, main, M.CREATE)
    assert "MAX_ITEMS" in related(db, main, M.USE)
    # declaration in the header is merged with the definition in the .cpp
    area = one(db, "geo::Rect::area")
    assert db.location(area).endswith("shape.cpp:32")
    assert [db.location(d) for d in db.declarations(area.id)] == ["include/shape.h:36"]
    assert db.parent(area).longname == "geo::Rect"
    # C function declared in a header, defined in a .c file
    clamp = one(db, "clamp")
    assert db.file(clamp.file_id).relpath == "src/util.c"
    # fields resolved through the enclosing class
    assert related(db, area, M.USE) == {"geo::Rect::w_", "geo::Rect::h_"}
    # includes
    f = one(db, "src/main.cpp")
    assert {"include/shape.h", "src/util.h"} <= related(db, f, M.INCLUDE)
    assert "iostream" in {x.name for x in db.related(f.id, (M.INCLUDE,))}
    # inheritance
    shape = one(db, "geo::Shape")
    assert related(db, shape, M.INHERIT, reverse=True) == {"geo::Circle", "geo::Rect"}


def test_python_resolution(py_db):
    db = py_db
    build = one(db, "main.build")
    assert {"pkg.shapes.Circle", "pkg.shapes.Rect"} <= related(db, build, M.CREATE)
    desc = one(db, "pkg.shapes.Shape.describe")
    assert "pkg.shapes.Shape.area" in related(db, desc, M.CALL)
    circle_init = one(db, "pkg.shapes.Circle.__init__")
    assert "pkg.shapes.Shape.__init__" in related(db, circle_init, M.CALL)
    circle = one(db, "pkg.shapes.Circle")
    assert related(db, circle, M.INHERIT) == {"pkg.shapes.Shape"}
    main = one(db, "main.py")
    imports = related(db, main, M.IMPORT)
    assert {"pkg.shapes.Circle", "pkg.shapes.Rect", "pkg/shapes.py"} <= imports
    # `c = Circle(2); c.describe()` resolves through the inferred type and base class
    assert "pkg.shapes.Shape.describe" in related(db, main, M.CALL)


def test_derived_metrics(cpp_db):
    db = cpp_db
    shape = one(db, "geo::Shape")
    m = db.metrics(shape.id)
    assert m["CountClassDerived"] == 2
    assert m["CountDeclMethod"] == 5
    rect = one(db, "geo::Rect")
    assert db.metric(rect.id, "MaxInheritanceTree") == 1
    classify = one(db, "classify")
    assert db.metric(classify.id, "Cyclomatic") == 7
    assert db.metric(classify.id, "CountInput") == 1
    pm = db.project_metrics()
    assert pm["Files"] == 5 and pm["Classes"] >= 3


def test_incremental_analysis(tmp_path):
    from codescope.core.analyzer import create_project
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def f():\n    return g()\n")
    (src / "b.py").write_text("def g():\n    return 1\n")
    db = create_project(str(tmp_path / "p.csdb"), "p", [str(src)])
    st = Analyzer(db).analyze()
    assert st["parsed"] == 2
    assert Analyzer(db).analyze()["parsed"] == 0
    f = one(db, "a.f")
    assert related(db, f, M.CALL) == {"b.g"}
    (src / "b.py").write_text("def h():\n    return 1\n\ndef g():\n    return h()\n")
    os.utime(src / "b.py", (1, 1))
    st = Analyzer(db).analyze()
    assert st["parsed"] == 1
    f = one(db, "a.f")
    assert related(db, f, M.CALL) == {"b.g"}
    assert related(db, one(db, "b.g"), M.CALL) == {"b.h"}
    os.remove(src / "b.py")
    assert Analyzer(db).analyze()["removed"] == 1
    assert {x.kind for x in db.related(one(db, "a.f").id, (M.CALL,))} == {M.UNRESOLVED_FUNCTION}
    db.close()


def test_control_flow(cpp_db):
    db = cpp_db
    cfg = build_cfg(db, one(db, "classify"))
    kinds = [n.kind for n in cfg.nodes]
    assert kinds.count("loop") == 1 and kinds.count("cond") == 2
    assert kinds.count("case") == 3 and "switch" in kinds
    labels = {lbl for _, _, lbl in cfg.edges}
    assert {"T", "F"} <= labels
    end = next(n.id for n in cfg.nodes if n.kind == "end")
    assert any(b == end for _, b, _ in cfg.edges)


def test_python_control_flow(py_db):
    db = py_db
    cfg = build_cfg(db, one(db, "main.build"))
    kinds = [n.kind for n in cfg.nodes]
    assert kinds.count("cond") == 2 and kinds.count("loop") == 1 and "return" in kinds


def test_graphs_and_svg(all_db):
    db = all_db
    for e in (one(db, "main"), one(db, "geo::Shape"), one(db, "cpp/src/main.cpp"), one(db, "pkg.shapes.Rect")):
        for kind in graphs_for(e):
            g = build_graph(db, kind, e)
            layout(g)
            assert g.nodes, (kind, e.longname)
            for n in g.nodes.values():
                assert n.x > 0 and n.y > 0
            svg = to_svg(g)
            assert svg.startswith("<svg") and svg.endswith("</svg>")
            assert "digraph" in to_dot(g)
    g = build_graph(db, "architecture", None)
    assert "cpp/src" in g.nodes


def test_layout_cycles():
    from codescope.core.layout import Graph
    g = Graph()
    for i in range(5):
        g.add_node(i, f"n{i}")
    for a, b in [(0, 1), (1, 2), (2, 0), (2, 3), (3, 3), (0, 4), (4, 3)]:
        g.add_edge(a, b)
    layout(g)
    ys = {n.id: n.y for n in g.nodes.values()}
    assert ys[0] < ys[1] < ys[2]
    # nodes on one rank do not overlap
    by_rank = {}
    for n in g.nodes.values():
        by_rank.setdefault(round(n.y), []).append(n)
    for row in by_rank.values():
        row.sort(key=lambda n: n.x)
        for a, b in zip(row, row[1:]):
            assert a.x + a.width / 2 <= b.x - b.width / 2 + 0.01


def test_layout_left_to_right_keeps_box_shape():
    from codescope.core.layout import Graph, measure
    g = Graph(direction="LR")
    for i in range(6):
        g.add_node(i, f"some/long/directory/name{i}/")
    for a, b in [(0, 1), (0, 2), (1, 3), (2, 3), (3, 4), (4, 5), (5, 0)]:
        g.add_edge(a, b)
    layout(g)
    for n in g.nodes.values():
        w, h = measure(n.label)
        assert (round(n.width), round(n.height)) == (round(w), round(h))     # not swapped
    by_col = {}
    for n in g.nodes.values():
        by_col.setdefault(round(n.x), []).append(n)
    for col in by_col.values():
        col.sort(key=lambda n: n.y)
        for a, b in zip(col, col[1:]):
            assert a.y + a.height / 2 <= b.y - b.height / 2 + 0.01


def test_dependency_graphs_stay_readable(all_db):
    from codescope.core.graphs import architecture_graph, auto_dir_level, file_dependency_graph
    level = auto_dir_level(all_db)
    g = architecture_graph(all_db, level)
    assert g.nodes and all(e.weight >= 1 for e in g.edges)
    assert len(g.edges) <= max(3 * len(g.nodes), 20)
    g = file_dependency_graph(all_db, max_nodes=3)
    assert len(g.nodes) <= 3


def test_call_tree(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_call_tree, layout_call_tree, to_svg, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.c").write_text(
        "int leaf(void) { return 1; }\n"
        "int mid(int n) { return n ? mid(n - 1) : leaf(); }\n"
        "int other(void) { return leaf(); }\n"
        "int top(void) { int x = mid(3); x += other(); x += leaf(); return x + mid(1); }\n"
        "int main(void) { return top() + other(); }\n")
    db = create_project(str(tmp_path / "t.csdb"), "t", [str(src)])
    Analyzer(db).analyze()
    top = one(db, "top")
    t = build_call_tree(db, top, descendants=True, ancestors=True, depth=5)
    # callees in source order, each distinct callee once per parent
    assert [c.entity.name for c in t.down] == ["mid", "other", "leaf"]
    mid = t.down[0]
    assert [c.entity.name for c in mid.children] == ["mid", "leaf"]
    assert mid.children[0].recursive                     # mid calls itself
    assert t.down[1].children[0].entity.name == "leaf"
    assert [c.entity.name for c in t.up] == ["main"]
    assert t.up[0].depth == -1 and t.down[0].depth == 1
    # depth limit marks truncation
    t1 = build_call_tree(db, one(db, "main"), depth=1)
    assert {c.entity.name for c in t1.down} == {"top", "other"} and any(c.truncated for c in t1.down)
    # layout: no overlaps within a column, parents centred on their children, ancestors on the left
    layout_call_tree(t)
    cols = {}
    for n in t.nodes():
        cols.setdefault(n.depth, []).append(n)
    for col in cols.values():
        col.sort(key=lambda n: n.y)
        for a, b in zip(col, col[1:]):
            assert a.y + a.height / 2 <= b.y - b.height / 2 + 0.01
    assert t.up[0].x < t.root.x < t.down[0].x
    assert abs(mid.y - (mid.children[0].y + mid.children[-1].y) / 2) < 0.01
    svg = to_svg(t, "tree")
    assert svg.startswith("<svg") and svg.count("<rect") == t.size + 1
    txt = to_text(t)
    assert "mid  ↻" in txt and "← main" in txt
    db.close()


def test_class_tree(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_class_tree, layout_call_tree, to_svg, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "h.hpp").write_text(
        "struct Base { virtual ~Base(); int id; virtual void run(); };\n"
        "struct Left : Base { void run() override; };\n"
        "struct Right : Base { int extra; };\n"
        "struct Diamond : Left, Right { };\n"
        "struct Leaf : Diamond { };\n"
        "class Lone { };\n")
    db = create_project(str(tmp_path / "c.csdb"), "c", [str(src)])
    Analyzer(db).analyze()
    base = one(db, "Base")
    t = build_class_tree(db, base, descendants=True, ancestors=True)
    # every data member is listed (here Base::id), followed by the derived classes
    assert [(c.entity.name, c.rel) for c in t.down] == [("id", "field"), ("Left", "inherit"), ("Right", "inherit")]
    assert t.down[0].via == "id : int"
    left, right = t.down[1:]
    assert [c.entity.name for c in left.children] == ["Diamond"]
    assert left.children[0].children[0].entity.name == "Leaf"
    # the diamond is expanded once and marked where it appears again
    assert [c.via for c in right.children if c.rel == "field"] == ["extra : int"]
    assert right.children[-1].entity.name == "Diamond" and right.children[-1].repeated
    assert t.up == []
    up = build_class_tree(db, one(db, "Leaf"), descendants=False, ancestors=True)
    assert [c.entity.name for c in up.up] == ["Diamond"]
    assert [c.entity.name for c in up.up[0].children] == ["Left", "Right"]
    m = build_class_tree(db, base, members=True)
    assert "+ run() {virtual}" in m.root.members and any(l.startswith("+ id") for l in m.root.members)
    layout_call_tree(m)
    assert m.root.height > 40                       # member card is taller than a plain box
    svg = to_svg(m, "classes")
    assert 'marker-start="url(#tri)"' in svg and "+ run() {virtual}" in svg
    assert "Data members and derived classes (descendants):" in to_text(t)
    inh = build_class_tree(db, base, relation="inherit")
    assert "Derived classes (descendants):" in to_text(inh)
    lone = build_class_tree(db, one(db, "Lone"))
    assert "no related classes found" in to_text(lone)
    db.close()


def test_class_tree_composition(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_class_tree, layout_call_tree, to_svg, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "m.hpp").write_text(
        "#include <vector>\n#include <memory>\n"
        "namespace eng {\n"
        "struct Link { double mass; };\n"
        "struct Joint { int type; };\n"
        "using Real = double;\n"
        "struct Builder { std::vector<Link> links; std::vector<Joint> joints; Real dt; };\n"
        "class Model { std::shared_ptr<const Builder> storage_; static int count; };\n"
        "class World { Model model_; };\n"
        "}\n")
    db = create_project(str(tmp_path / "c.csdb"), "c", [str(src)])
    Analyzer(db).analyze()
    model = one(db, "eng::Model")
    t = build_class_tree(db, model, hide_unresolved=True)
    assert [(c.entity.name, c.via, c.rel) for c in t.down] == [("Builder", "storage_*", "compose"),
                                                                ("count", "count : int {static}", "field")]
    parts = {c.entity.name: c.via for c in t.down[0].children}
    assert parts == {"Link": "links[]", "Joint": "joints[]", "dt": "dt : Real"}   # every data member
    assert [(c.entity.name, c.via) for c in t.up] == [("World", "model_")]
    text = to_text(t)
    assert "storage_* : Builder" in text and "World.model_" in text
    # external types (double behind the Real alias) are listed unless hidden
    full = build_class_tree(db, one(db, "eng::Builder"), ancestors=False)
    assert any(c.via == "dt : Real" and c.rel == "field" for c in full.down)
    # inheritance only: a class without bases has nothing to show
    assert build_class_tree(db, model, relation="inherit").down == []
    layout_call_tree(t)
    assert 'marker-start="url(#dia)"' in to_svg(t, "Model")
    db.close()


MODIFY_CPP = """
#include <vector>
struct P { double m; };
struct Body { std::vector<P> parts; double mass; int n; void reset(); };
void Body::reset() { parts.clear(); mass = 0; n = 0; }
void grow(Body& b) { b.parts.push_back(P{}); b.n++; }
void scale(Body* b) { b->mass *= 2; b->parts[0].m = 1; }
void step(Body& b) { grow(b); scale(&b); b.reset(); }
int main() { Body b; b.reset(); for (int i = 0; i < 3; ++i) step(b); return 0; }
"""

MODIFY_PY = """
class Store:
    def __init__(self):
        self.items = []
        self.meta = {}

    def add(self, x):
        self.items.append(x)
        self.meta["n"] = len(self.items)

    def drop(self, k):
        del self.meta[k]


def run():
    s = Store()
    s.add(1)
    s.drop("n")
"""


def test_modification_tree(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_modify_tree, layout_call_tree, to_svg, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "m.cpp").write_text(MODIFY_CPP)
    (src / "store.py").write_text(MODIFY_PY)
    db = create_project(str(tmp_path / "c.csdb"), "c", [str(src)])
    Analyzer(db).analyze()

    def writes_of(name):
        return {(r.name, db.entity(r.src_id).name) for r in db._refs(
            "SELECT id, kind, src_id, dst_id, name, qualifier, file_id, line, col FROM refs "
            "WHERE kind='Set' AND name=?", (name,))}
    # member chains, subscripts and mutating calls count as writes; '->' stops at the pointer
    assert ("parts", "grow") in writes_of("parts") and ("parts", "reset") in writes_of("parts")
    assert ("parts", "scale") in writes_of("parts") and ("m", "scale") in writes_of("m")
    assert ("b", "grow") in writes_of("b") and ("b", "scale") not in writes_of("b")
    assert {("items", "__init__"), ("items", "add")} <= writes_of("items")
    assert {("meta", "add"), ("meta", "drop")} <= writes_of("meta")

    body = one(db, "Body")
    t = build_modify_tree(db, body)
    assert t.writers == 3 and [n.entity.name for n in t.down] == ["main"]
    main = t.down[0]
    assert [n.entity.name for n in main.children] == ["reset", "step"]
    order = [(n.step, sorted({w for w, _, _ in n.writes})) for n in t.nodes() if n.rel == "write"]
    assert order == [(1, ["mass", "n", "parts"]), (2, ["n", "parts"]), (3, ["mass", "parts"])]
    step = main.children[1]
    assert [n.entity.name for n in step.children] == ["grow", "scale", "reset"]
    assert step.children[2].repeated and step.children[2].via == "repeats #1"   # reset() shown under main
    text = to_text(t)
    assert "#3  sets mass, parts" in text and "Modified in" in text
    mass = next(f for f in db.children(body.id) if f.name == "mass")
    only = build_modify_tree(db, body, mass)
    assert [n.step for n in only.nodes() if n.rel == "write"] == [1, 2]
    assert "Body::mass" in to_text(only)
    flat = build_modify_tree(db, body, paths=False)
    assert [n.entity.name for n in flat.down] == ["reset", "grow", "scale"]
    layout_call_tree(t)
    assert "#1  sets parts, mass, n" in to_svg(t, "Body")

    store = one(db, "store.Store")
    ts = build_modify_tree(db, store)
    assert [n.entity.name for n in ts.down] == ["run"]
    calls = [n.entity.name for n in ts.down[0].children]
    assert calls == ["__init__", "add", "drop"]           # Store() reaches the constructor
    assert [n.step for n in ts.nodes() if n.rel == "write"] == [1, 2, 3]
    empty = build_modify_tree(db, one(db, "P"), paths=False)
    assert empty.writers == 1                              # only scale writes P::m
    db.close()


WHOLE_CPP = """
#include <vector>
struct Settings { double damping; };
struct Options { Settings settings; double dt; };
struct Sim { Options options; std::vector<Settings> history; Settings* current; };
void configure(Sim& s, const Options& o) { s.options = o; }
void tweak(Sim& s) { s.options.dt = 0.1; }
void tune(Sim& s) { s.options.settings.damping = 2; }
void record(Sim& s) { s.history.push_back(Settings{}); s.current = nullptr; }
int main() { Sim s; Options o; configure(s, o); tweak(s); tune(s); record(s); return 0; }
"""


def test_modification_tree_whole_object(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_modify_tree, holder_paths, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "w.cpp").write_text(WHOLE_CPP)
    db = create_project(str(tmp_path / "c.csdb"), "c", [str(src)])
    Analyzer(db).analyze()
    settings = one(db, "Settings")
    # held by value directly and through Options; the vector and the pointer do not count
    assert sorted(p for _, p in holder_paths(db, settings)) == ["options.settings", "settings"]
    t = build_modify_tree(db, settings)
    main = t.down[0]
    assert [c.entity.name for c in main.children] == ["configure", "tune"]   # not tweak, not record
    steps = [(n.step, n.writes) for n in t.nodes() if n.rel == "write"]
    assert steps == [(1, [("options.settings", 6, True)]), (2, [("damping", 8, False)])]
    text = to_text(t)
    assert "#1  replaces options.settings  (line 6)" in text and "#2  sets damping" in text
    damping = next(f for f in db.children(settings.id) if f.name == "damping")
    assert [n.step for n in build_modify_tree(db, settings, damping).nodes() if n.rel == "write"] == [1, 2]
    direct = build_modify_tree(db, settings, whole=False)
    assert [c.entity.name for c in direct.down[0].children] == ["tune"]
    db.close()


MEMBERS_CPP = """
#include <vector>
#include <memory>
struct Part { double mass; void scale(double f); };
struct Base { int id; };
struct Holder { std::vector<Part> parts; std::shared_ptr<Part> main; int count() const; static int total; };
class Machine : public Base { public: Holder holder; void run(); private: std::vector<int> ids; };
struct Factory { Machine machine; };
struct Plant { Factory factory; };
"""


def test_member_tree(tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import build_member_tree, layout_call_tree, to_svg, to_text
    src = tmp_path / "s"
    src.mkdir()
    (src / "m.hpp").write_text(MEMBERS_CPP)
    db = create_project(str(tmp_path / "c.csdb"), "c", [str(src)])
    Analyzer(db).analyze()
    t = build_member_tree(db, one(db, "Machine"))
    assert [c.via for c in t.down] == ["+ holder : Holder", "+ run()", "- ids[] : vector<int>"]
    holder = t.down[0]
    assert [c.via for c in holder.children] == ["+ parts[] : Part", "+ main* : Part", "+ count() : int",
                                                "+ total : int {static}"]
    parts, main = holder.children[:2]
    assert [c.via for c in parts.children] == ["+ mass : double", "+ scale()"]
    assert main.repeated and not main.children          # Part already expanded under parts
    # ancestors: only the direct base and the direct container, not Plant
    assert sorted((c.entity.name, c.rel) for c in t.up) == [("Base", "inherit"), ("Factory", "compose")]
    shallow = build_member_tree(db, one(db, "Machine"), depth=1)
    assert shallow.down[0].truncated and not shallow.down[0].children
    text = to_text(t)
    assert "Members (descendants):" in text and "Direct base classes and containers" in text
    layout_call_tree(t)
    assert "+ parts[] : Part" in to_svg(t, "Machine")
    db.close()


def test_grow_single_branch(cpp_db, tmp_path):
    from codescope.core.analyzer import create_project
    from codescope.core.calltree import (build_call_tree, build_member_tree, can_grow, grow, grow_branch,
                                         to_text)
    main = one(cpp_db, "main")
    t = build_call_tree(cpp_db, main, depth=1, hide_unresolved=True)
    cut = [n for n in t.down if n.truncated]
    assert cut and all(not n.children for n in t.down)
    n = cut[0]
    assert can_grow(t, n)
    added = grow(cpp_db, t, n, 1, hide_unresolved=True)
    assert added == len(n.children) > 0 and not n.truncated
    assert all(c.depth == 2 for c in n.children)              # depths continue from the node
    assert all(not o.children for o in t.down if o is not n)  # the rest of the tree is unchanged
    assert "▸" not in to_text(t).split(n.label)[1].split("\n")[0]
    # ancestors: depths stay negative
    area = one(cpp_db, "geo::Rect::area")
    up = build_call_tree(cpp_db, area, descendants=False, ancestors=True, depth=1)
    u = next((x for x in up.up if can_grow(up, x)), None)
    if u is not None:
        grow(cpp_db, up, u, 2)
        assert all(c.depth <= -2 for c in u.children)
    # member trees: a cut-off data member grows into the members of its type
    src = tmp_path / "m"
    src.mkdir()
    (src / "m.hpp").write_text(MEMBERS_CPP)
    db = create_project(str(tmp_path / "m.csdb"), "m", [str(src)])
    Analyzer(db).analyze()
    mt = build_member_tree(db, one(db, "Machine"), depth=1)
    holder = mt.down[0]
    assert holder.truncated and can_grow(mt, holder)
    grow(db, mt, holder, 1)
    assert [c.via for c in holder.children][:2] == ["+ parts[] : Part", "+ main* : Part"]
    assert all(c.truncated for c in holder.children[:1])
    assert grow_branch(db, mt, holder) > 0 and holder.children[0].children
    db.close()
