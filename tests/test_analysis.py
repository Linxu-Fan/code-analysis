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
