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
