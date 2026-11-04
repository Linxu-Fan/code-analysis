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
    assert not win.info.isHeaderHidden()
    win.info.header().resizeSection(0, 260)            # dragging the column divider
    win.info.show_entity(main.id)
    assert win.info.columnWidth(0) == 260
    # ctrl+click style navigation from a call site to the definition
    text = open(win.db.file(main.file_id).path).read().split("\n")
    line = next(i for i, l in enumerate(text, 1) if "classify(argc)" in l)
    win._editor_jump(main.file_id, line, text[line - 1].index("classify(argc)") + 2)
    assert win.db.entity(win.current_entity).name == "classify"
    for kind in ("calls", "callby", "butterfly", "control"):
        win.open_graph(kind, main.id)
        assert isinstance(win.tabs.currentWidget(), GraphPanel)
        assert win.tabs.currentWidget().graph.nodes
    from codescope.gui.calltree_view import CallTreePanel
    win.open_call_tree(main)
    ct = win.tabs.currentWidget()
    assert isinstance(ct, CallTreePanel) and ct.tree is not None and ct.tree.down
    ct.up.setChecked(True)
    ct.down.setChecked(False)
    assert not ct.tree.down
    classify = next(e for e in win.db.entities(name="classify"))
    ct._picked(classify.longname)
    assert ct.entity.id == classify.id and [n.entity.name for n in ct.tree.up] == ["main"]
    ct._collapse(ct.tree.up[0], True)
    ct.up.setChecked(False)
    ct.down.setChecked(True)
    ct.set_function(next(e for e in win.db.entities(name="main") if e.kind == "Function" and e.lang == "cpp"))
    ct.depth.setValue(1)
    cut = next(n for n in ct.tree.down if n.truncated)
    ct._grow(cut, 1)
    assert cut.children and ct.depth.value() == 1 and cut in ct.tree.nodes()
    shape = next(e for e in win.db.entities(name="Shape") if e.kind == "Class")
    win.open_class_tree(shape)
    cls_panel = win.tabs.currentWidget()
    assert cls_panel.mode == "class" and {n.entity.name for n in cls_panel.tree.down if n.rel == "inherit"} == {"Circle", "Rect"}
    cls_panel.members.setChecked(True)
    assert cls_panel.tree.root.members
    cls_panel.relation.setCurrentIndex(cls_panel.relation.findData("members"))
    assert cls_panel.tree.relation == "members" and cls_panel.depth.value() == 3
    assert any(n.entity.kind == "Method" for n in cls_panel.tree.down) and cls_panel.members.isHidden()
    rect = next(e for e in win.db.entities(name="Rect") if e.kind == "Class")
    win.open_modify_tree(rect)
    mod = win.tabs.currentWidget()
    assert mod.mode == "modify" and mod.tree.writers >= 2
    assert mod.member.count() > 3                         # all members + w_, h_ and inherited ones
    mod.paths.setChecked(False)
    assert "Rect::Rect" in {n.label for n in mod.tree.down}
    w = next(f for f in win.db.children(rect.id) if f.name == "w_")
    win.open_modify_tree(w)
    assert win.tabs.currentWidget().member.currentData() == w.id
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


VSSETTINGS = ('<?xml version="1.0" encoding="utf-8"?><UserSettings><Category name="Environment_FontsAndColors">'
              '<FontsAndColors Version="2.0"><Categories>'
              '<Category GUID="{58E96763-1D3B-4E05-B6BA-FF7115FD0B7B}" FontName="Segoe UI" FontSize="9" />'
              '<Category GUID="{A27B4E24-A735-4D1D-B8E7-9716E1E3D8E0}" FontName="{font}" FontSize="{size}" '
              'CharSet="1" FontIsDefault="No"><Items /></Category></Categories></FontsAndColors></Category>'
              '</UserSettings>')


def test_visual_studio_font(app, tmp_path, monkeypatch):
    import time

    from codescope.gui import code_editor as CE
    from codescope.gui import theme
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert CE.visual_studio_font() is None                     # Visual Studio not installed
    old = tmp_path / "Microsoft" / "VisualStudio" / "16.0_abc" / "Settings"
    new = tmp_path / "Microsoft" / "VisualStudio" / "17.0_def" / "Settings"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "CurrentSettings.vssettings").write_text(VSSETTINGS.replace("{font}", "Consolas").replace("{size}", "11"))
    time.sleep(0.05)
    # Visual Studio writes UTF-8 or UTF-16 files; the newest version's settings win
    (new / "CurrentSettings.vssettings").write_bytes(
        VSSETTINGS.replace("{font}", "Cascadia Mono").replace("{size}", "10.5").encode("utf-16"))
    assert CE.visual_studio_font() == ("Cascadia Mono", 10.5)
    (new / "CurrentSettings.vssettings").write_text("<UserSettings/>")
    assert CE.visual_studio_font() == ("Consolas", 11.0)
    assert not CE._installed("No Such Font 123")
    monkeypatch.setitem(theme.current, "editor_font", ("No Such Font 123", 12.0))
    f = CE.mono_font()
    assert f.family() != "No Such Font 123" and f.pointSizeF() == 12.0 and f.fixedPitch()


def test_external_file_changes(app, tmp_path, monkeypatch):
    import os
    import shutil
    import time

    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "settings"))
    from PySide6.QtWidgets import QMessageBox

    from codescope.core.analyzer import create_project
    from codescope.gui.main_window import MainWindow

    from .conftest import SAMPLES
    src = tmp_path / "src"
    shutil.copytree(os.path.join(SAMPLES, "cpp"), src)
    path = str(tmp_path / "x.csdb")
    create_project(path, "x", [str(src)]).close()
    win = MainWindow()
    win.show()
    win.open_project(path)
    _wait(app, win)
    f = next(x for x in win.db.files() if x.relpath.endswith("shape.cpp"))
    _idx, pane = win.editor_for(f.id)
    ed = pane.editor
    assert f.path in win.watcher.files()
    # another program edits the file: the editor reloads it and the analysis catches up
    time.sleep(0.02)
    with open(f.path, "a", encoding="utf-8") as fh:
        fh.write("\nint addedExternally() { return 42; }\n")
    os.utime(f.path, None)
    win.check_external_changes()
    assert "addedExternally" in ed.toPlainText() and not ed.document().isModified()
    deadline = time.time() + 30
    while time.time() < deadline and not win.db.entities(name="addedExternally"):
        app.processEvents()
        time.sleep(0.02)
    _wait(app, win)                                    # the background re-analysis has finished
    assert win.db.entities(name="addedExternally")
    assert not any(type(win.tabs.widget(i)).__name__ == "ProjectSummary" and i == win.tabs.currentIndex()
                   for i in range(win.tabs.count()))          # quiet: the editor stays in front
    # unsaved edits: ask first, keep them when the user says no
    ed.insertPlainText("// my edit\n")
    with open(f.path, "a", encoding="utf-8") as fh:
        fh.write("// theirs\n")
    os.utime(f.path, (time.time() + 5, time.time() + 5))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.check_external_changes()
    assert "// my edit" in ed.toPlainText() and "// theirs" not in ed.toPlainText()
    win.check_external_changes()                       # not asked again for the same version
    # saving here is not an external change
    ed.save()
    assert not ed.changed_on_disk()
    win.close_project()
    assert not win.watcher.files()
    win.close()
