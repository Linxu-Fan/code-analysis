import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait(app, win):
    import time
    deadline = time.time() + 60
    while win.worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(5):
        app.processEvents()


def test_main_window_workflow(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    from codescope.core.analyzer import create_project
    from codescope.gui.code_editor import EditorPane
    from codescope.gui.graph_view import GraphPanel
    from codescope.gui.main_window import MainWindow

    from .conftest import SAMPLES
    path = str(tmp_path / "g.csdb")
    create_project(path, "gui", [SAMPLES]).close()
    win = MainWindow()
    win.show()
    win.open_project(path)
    _wait(app, win)
    assert win.db is not None and win.db.get_meta("linked") == "1"
    from codescope.gui.metrics_view import ProjectSummary
    overview = win.tabs.currentWidget()
    assert isinstance(overview, ProjectSummary)
    assert overview.sunburst.root["value"] > 0
    assert [l for l, _, _ in overview.lines.items][:2] == ["Code", "Comment"]
    assert overview.bubbles.items[0][0] == "classify"
    overview.sunburst.grab()
    overview.bubbles.grab()
    assert overview.sunburst.segments and len(overview.bubbles.circles) == len(overview.bubbles.items)
    main = next(e for e in win.db.entities(name="main") if e.kind == "Function")
    win.activate_entity(main.id)
    pane = win.tabs.currentWidget()
    assert isinstance(pane, EditorPane)
    assert pane.editor.textCursor().blockNumber() + 1 == main.line
    assert win.info.topLevelItem(0).text(0) == "main"
    # ctrl+click style navigation from a call site to the definition
    text = open(win.db.file(main.file_id).path).read().split("\n")
    line = next(i for i, l in enumerate(text, 1) if "classify(argc)" in l)
    win._editor_jump(main.file_id, line, text[line - 1].index("classify(argc)") + 2)
    assert win.db.entity(win.current_entity).name == "classify"
    for kind in ("calls", "callby", "butterfly", "control"):
        win.open_graph(kind, main.id)
        assert isinstance(win.tabs.currentWidget(), GraphPanel)
        assert win.tabs.currentWidget().graph.nodes
    win.open_graph("architecture", None)
    panel = win.tabs.currentWidget()
    some = next(iter(panel.canvas.node_items))
    panel.canvas.highlight(some)
    panel.canvas.highlight(None)
    assert panel.canvas.find("src") >= 1
    win.a_metrics.trigger()
    win.a_treemap.trigger()
    win.a_dsm.trigger()
    win.open_codecheck()
    panel = win.tabs.currentWidget()
    panel._run()
    _wait(app, win)
    assert panel.results.topLevelItemCount() >= 3
    win.find_in_files("area")
    assert win.find_panel.results.topLevelItemCount() >= 2
    win.a_dark.setChecked(True)
    win.toggle_theme()
    win.go_back()
    win.close_project()
    assert win.db is None
    win.close()


def test_project_dialog_folder_picker(app, tmp_path):
    from PySide6.QtCore import Qt
    from codescope.core.folders import norm
    from codescope.gui.panels import ProjectDialog

    from .test_tools import _make_tree
    root = _make_tree(tmp_path)
    dlg = ProjectDialog(None, {"name": "p", "roots": [str(root)]}, str(tmp_path / "p.csdb"), new=True)
    picker = dlg.picker
    top = picker.tree.topLevelItem(0)
    names = {top.child(i).text(0).split()[0]: top.child(i) for i in range(top.childCount())}
    assert {"src", "include", "third_party", "build"} <= set(names) and "docs" not in names
    # existing project without a saved selection: everything selected
    assert picker.selection() == [] and picker.selected_files() == picker.total_files() == 6
    picker.select_own()
    assert names["third_party"].checkState(0) == Qt.Unchecked
    assert names["src"].checkState(0) == Qt.Checked
    picker._set_all(Qt.Unchecked)
    names["src"].setCheckState(0, Qt.Checked)
    names["include"].setCheckState(0, Qt.Checked)
    sel = dlg.result_config()["folders"]
    assert sel == [{"path": norm(root / "include"), "recursive": True},
                   {"path": norm(root / "src"), "recursive": True}]
    assert picker.selected_files() == 3
    # reopening restores the ticks
    dlg2 = ProjectDialog(None, {"name": "p", "roots": [str(root)], "folders": sel}, None, new=False)
    assert dlg2.picker.selection() == sel


def test_editor_visual_studio_highlighting(app, cpp_db):
    from codescope.gui import theme
    from codescope.gui.code_editor import EditorPane
    theme.current["editor_scheme"] = "vs-light"
    f = next(x for x in cpp_db.files() if x.relpath.endswith("main.cpp"))
    pane = EditorPane(f.path, f.id, f.lang)
    ed = pane.editor
    rows = cpp_db.conn.execute("SELECT r.line, r.col, r.name, e.kind FROM refs r JOIN entities e ON e.id=r.dst_id "
                               "WHERE r.file_id=?", (f.id,)).fetchall()
    ed.set_semantic(rows, frozenset({"Shape", "Rect", "Circle"}), frozenset({"MAX_ITEMS"}))

    def color_at(line, word):
        blk = ed.document().findBlockByNumber(line - 1)
        i = blk.text().index(word)
        for r in blk.layout().formats():
            if r.start <= i < r.start + r.length:
                c = r.format.foreground().color().name()
        return c

    text = open(f.path).read().split("\n")
    ln = next(i for i, l in enumerate(text, 1) if "static int classify" in l)
    scheme = theme.CODE_SCHEMES["vs-light"]
    assert color_at(ln, "static") == scheme["keyword"]
    assert color_at(ln, "classify") == scheme["function"]
    ln = next(i for i, l in enumerate(text, 1) if "new Rect" in l)
    assert color_at(ln, "Rect") == scheme["type"]
    ln = next(i for i, l in enumerate(text, 1) if "MAX_ITEMS" in l)
    assert color_at(ln, "MAX_ITEMS") == scheme["macro"]
    assert color_at(1, "#include") == scheme["preproc"]
    assert color_at(1, "<iostream>") == scheme["string"]
    assert ed.indent_unit() == 4
    ed.zoom(2)
    ed.zoom_reset()
    pane.grab()
    theme.current["editor_scheme"] = "auto"
