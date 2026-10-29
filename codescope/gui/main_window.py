"""Main application window."""

from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QCompleter, QDockWidget, QFileDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QTabWidget,
                               QTextBrowser, QToolBar, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import model as M
from ..core.database import DEFAULT_CONFIG, Database
from ..core.graphs import GRAPH_TYPES, graphs_for
from . import theme
from .browsers import EntityFilter, InfoBrowser, ProjectTree
from .code_editor import EditorPane
from .common import Worker, app_icon
from .graph_view import GraphPanel
from .metrics_view import DependencyMatrix, MetricsBrowser, ProjectSummary, TreemapView
from .panels import CodeCheckPanel, FindInFiles, ProjectDialog

MAX_RECENT = 10


class WelcomePage(QTextBrowser):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.title = "Welcome"
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._link)
        self.refresh()

    def refresh(self):
        recent = self.win.recent_projects()
        items = "".join(f"<li><a href='open:{i}'>{os.path.basename(p)}</a> <span style='color:#888'>{p}</span></li>"
                        for i, p in enumerate(recent)) or "<li style='color:#888'>No recent projects</li>"
        fg = "#dfe1e5" if theme.current["name"] == "dark" else "#1f2328"
        self.setHtml(f"""<div style='margin:30px;color:{fg};font-family:Segoe UI,sans-serif'>
            <h1 style='font-weight:600'>{APP_NAME}</h1>
            <p style='color:#888'>Static analysis and code comprehension for C, C++ and Python &mdash; version {__version__}</p>
            <h3>Start</h3><ul>
            <li><a href='new:'>Create a new project&hellip;</a> (Ctrl+N)</li>
            <li><a href='openfile:'>Open an existing project&hellip;</a> (Ctrl+O)</li></ul>
            <h3>Recent projects</h3><ul>{items}</ul>
            <h3>Tips</h3><ul>
            <li>Ctrl+click or F12 on an identifier jumps to its definition.</li>
            <li>Right-click any entity for its graphs: call trees, butterfly, inheritance, UML and control flow.</li>
            <li>Ctrl+Shift+F searches all project files; Ctrl+E finds an entity by name.</li>
            <li>The Metrics menu offers a sortable metrics browser, a treemap and a dependency matrix.</li></ul></div>""")

    def _link(self, url):
        s = url.toString()
        if s.startswith("new:"):
            self.win.new_project()
        elif s.startswith("openfile:"):
            self.win.open_project_dialog()
        elif s.startswith("open:"):
            recent = self.win.recent_projects()
            i = int(s[5:])
            if i < len(recent):
                self.win.open_project(recent[i])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("CodeScope", "CodeScope")
        self.db: Database | None = None
        self.worker = None
        self.nav_back, self.nav_fwd = [], []
        self.info_back, self.info_fwd = [], []
        self.current_entity = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setAcceptDrops(True)
        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("windowState")
        if state is not None:
            self.restoreState(state)
        self._update_actions()

    # ---- construction -------------------------------------------------------------------------
    def _build_central(self):
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)
        self.welcome = WelcomePage(self)
        self.tabs.addTab(self.welcome, "Welcome")

    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        self.project_tree = ProjectTree()
        self.entity_filter = EntityFilter()
        self.info = InfoBrowser()
        self.find_panel = FindInFiles()
        self.dock_project = self._dock("Project", self.project_tree, Qt.LeftDockWidgetArea, "dock_project")
        self.dock_entities = self._dock("Entities", self.entity_filter, Qt.LeftDockWidgetArea, "dock_entities")
        self.tabifyDockWidget(self.dock_project, self.dock_entities)
        self.dock_project.raise_()
        info_box = QWidget()
        iv = QVBoxLayout(info_box)
        iv.setContentsMargins(0, 0, 0, 0)
        bar = QToolBar()
        bar.setIconSize(bar.iconSize() * 0.8)
        self.info_back_act = bar.addAction(self.style().standardIcon(self.style().StandardPixmap.SP_ArrowBack),
                                           "Previous entity", self._info_back)
        self.info_fwd_act = bar.addAction(self.style().standardIcon(self.style().StandardPixmap.SP_ArrowForward),
                                          "Next entity", self._info_fwd)
        bar.addSeparator()
        self.info_graph_btn = QPushButton("Graphs ▾")
        self.info_graph_btn.setFlat(True)
        self.info_graph_btn.clicked.connect(self._info_graph_menu)
        bar.addWidget(self.info_graph_btn)
        iv.addWidget(bar)
        iv.addWidget(self.info)
        self.dock_info = self._dock("Information", info_box, Qt.RightDockWidgetArea, "dock_info")
        self.dock_find = self._dock("Find in Files", self.find_panel, Qt.BottomDockWidgetArea, "dock_find")
        self.dock_find.hide()
        for w in (self.project_tree, self.entity_filter, self.info):
            w.entitySelected.connect(self.show_entity)
            w.entityActivated.connect(self.activate_entity)
            w.contextRequested.connect(self.entity_menu)
        self.info.locationActivated.connect(self.open_location)
        self.find_panel.locationActivated.connect(self.open_location)

    def _act(self, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        return a

    def _build_actions(self):
        st = self.style().standardIcon
        SP = self.style().StandardPixmap
        self.a_new = self._act("&New Project…", self.new_project, "Ctrl+N")
        self.a_new.setIcon(st(SP.SP_FileIcon))
        self.a_open = self._act("&Open Project…", self.open_project_dialog, "Ctrl+O")
        self.a_open.setIcon(st(SP.SP_DialogOpenButton))
        self.a_close = self._act("&Close Project", self.close_project)
        self.a_save = self._act("&Save File", self.save_current, "Ctrl+S")
        self.a_save.setIcon(st(SP.SP_DialogSaveButton))
        self.a_exit = self._act("E&xit", self.close, "Ctrl+Q")
        self.a_config = self._act("&Configure Project…", self.configure_project)
        self.a_analyze = self._act("&Analyze Changed Files", lambda: self.analyze(False), "F5")
        self.a_analyze.setIcon(st(SP.SP_BrowserReload))
        self.a_full = self._act("Analyze &All Files", lambda: self.analyze(True), "Ctrl+F5")
        self.a_overview = self._act("Project &Overview", self.show_overview)
        self.a_find = self._act("&Find in Files…", self.find_in_files, "Ctrl+Shift+F")
        self.a_find_entity = self._act("Find &Entity…", lambda: (self.quick.setFocus(), self.quick.selectAll()),
                                       "Ctrl+E")
        self.a_back = self._act("&Back", self.go_back, "Alt+Left")
        self.a_back.setIcon(st(SP.SP_ArrowBack))
        self.a_fwd = self._act("&Forward", self.go_forward, "Alt+Right")
        self.a_fwd.setIcon(st(SP.SP_ArrowForward))
        self.a_metrics = self._act("&Metrics Browser", lambda: self.add_tab(MetricsBrowser(self.db), connect=True))
        self.a_treemap = self._act("Metrics &Treemap", lambda: self.add_tab(TreemapView(self.db), connect=True))
        self.a_dsm = self._act("&Dependency Matrix", lambda: self.add_tab(DependencyMatrix(self.db), connect=True))
        self.a_export_metrics = self._act("&Export Metrics (CSV)…", self.export_metrics)
        self.a_arch = self._act("&Architecture Graph", lambda: self.open_graph("architecture", None))
        self.a_uml = self._act("Project &UML Class Diagram", lambda: self.open_graph("uml", None))
        self.a_codecheck = self._act("&CodeCheck…", self.open_codecheck, "Ctrl+Shift+K")
        self.a_report = self._act("&HTML Report…", self.html_report)
        self.a_dark = self._act("&Dark Theme", self.toggle_theme)
        self.a_dark.setCheckable(True)
        self.a_about = self._act("&About", self.about)
        self.a_docs = self._act("&Command Line Usage", self.cli_help)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            m.addAction(a)
        self.recent_menu = m.addMenu("Recent Projects")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addAction(self.a_close)
        m.addSeparator()
        m.addAction(self.a_save)
        m.addSeparator()
        m.addAction(self.a_exit)
        m = mb.addMenu("&Project")
        for a in (self.a_config, None, self.a_analyze, self.a_full, None, self.a_overview):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Search")
        for a in (self.a_find, self.a_find_entity, None, self.a_back, self.a_fwd):
            m.addSeparator() if a is None else m.addAction(a)
        self.graph_menu = mb.addMenu("&Graphs")
        self.graph_menu.aboutToShow.connect(self._fill_graph_menu)
        m = mb.addMenu("&Metrics")
        for a in (self.a_metrics, self.a_treemap, self.a_dsm, None, self.a_export_metrics):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Checks")
        m.addAction(self.a_codecheck)
        m = mb.addMenu("&Reports")
        m.addAction(self.a_report)
        m = mb.addMenu("&View")
        m.addAction(self.a_dark)
        m.addSeparator()
        for d in (self.dock_project, self.dock_entities, self.dock_info, self.dock_find):
            m.addAction(d.toggleViewAction())
        m = mb.addMenu("&Help")
        m.addAction(self.a_docs)
        m.addAction(self.a_about)
        self.a_dark.setChecked(theme.current["name"] == "dark")

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("toolbar_main")
        self.addToolBar(tb)
        for a in (self.a_new, self.a_open, self.a_save, None, self.a_analyze, None, self.a_back, self.a_fwd):
            tb.addSeparator() if a is None else tb.addAction(a)
        tb.addSeparator()
        self.quick = QLineEdit(placeholderText="Find entity (Ctrl+E)…", clearButtonEnabled=True)
        self.quick.setMaximumWidth(340)
        self.quick.returnPressed.connect(self._quick_find)
        tb.addWidget(self.quick)
        tb.addSeparator()
        for text, fn in (("Metrics", self.a_metrics.trigger), ("Treemap", self.a_treemap.trigger),
                         ("CodeCheck", self.a_codecheck.trigger), ("Architecture", self.a_arch.trigger)):
            b = QPushButton(text)
            b.setFlat(True)
            b.clicked.connect(fn)
            tb.addWidget(b)
            setattr(self, f"btn_{text.lower()}", b)

    def _build_status(self):
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)

    def _update_actions(self):
        has = self.db is not None
        busy = self.worker is not None
        for a in (self.a_close, self.a_config, self.a_analyze, self.a_full, self.a_overview, self.a_find,
                  self.a_metrics, self.a_treemap, self.a_dsm, self.a_export_metrics, self.a_arch, self.a_uml,
                  self.a_codecheck, self.a_report, self.a_find_entity):
            a.setEnabled(has and not (busy and a in (self.a_analyze, self.a_full, self.a_config)))
        for b in (self.btn_metrics, self.btn_treemap, self.btn_codecheck, self.btn_architecture):
            b.setEnabled(has)
        self.quick.setEnabled(has)
        self.a_new.setEnabled(not busy)
        self.a_open.setEnabled(not busy)

    # ---- project lifecycle --------------------------------------------------------------------------
    def recent_projects(self):
        r = self.settings.value("recent", []) or []
        if isinstance(r, str):
            r = [r]
        return [p for p in r if os.path.exists(p)]

    def _add_recent(self, path):
        r = [p for p in self.recent_projects() if os.path.abspath(p) != os.path.abspath(path)]
        r.insert(0, os.path.abspath(path))
        self.settings.setValue("recent", r[:MAX_RECENT])

    def _fill_recent(self):
        self.recent_menu.clear()
        for p in self.recent_projects():
            self.recent_menu.addAction(p, lambda p=p: self.open_project(p))
        if not self.recent_menu.actions():
            self.recent_menu.addAction("(none)").setEnabled(False)

    def new_project(self):
        dlg = ProjectDialog(self, {"languages": list(DEFAULT_CONFIG["languages"]), "exclude": list(DEFAULT_CONFIG["exclude"])})
        if not dlg.exec():
            return
        cfg = dlg.result_config()
        path = dlg.db_path.text().strip()
        if not path.endswith(".csdb"):
            path += ".csdb"
        if os.path.exists(path) and QMessageBox.question(
                self, "Overwrite", f"{path} exists. Replace it?") != QMessageBox.Yes:
            return
        self.close_project()
        try:
            db = Database.create(path, cfg)
            db.close()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot create project", str(exc))
            return
        self.open_project(path, analyze=True)

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "CodeScope project (*.csdb);;All files (*)")
        if path:
            self.open_project(path)

    def open_project(self, path, analyze=False):
        self.close_project()
        try:
            self.db = Database(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Cannot open project", f"{path}\n\n{exc}")
            return
        self._add_recent(path)
        self.setWindowTitle(f"{self.db.config().get('name')} — {APP_NAME}")
        self.info.set_db(self.db)
        self.find_panel.set_db(self.db)
        self._update_actions()
        if analyze or self.db.get_meta("linked") != "1":
            self.analyze(False)
        else:
            self.reload_views()
            self.show_overview()

    def close_project(self):
        if self.worker is not None:
            return
        if not self._confirm_unsaved():
            return
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if w is not self.welcome:
                w.deleteLater()
        if self.db is not None:
            self.db.close()
        self.db = None
        self.current_entity = None
        self.nav_back.clear()
        self.nav_fwd.clear()
        self.info_back.clear()
        self.info_fwd.clear()
        for w in (self.project_tree, self.entity_filter, self.info, self.find_panel):
            w.set_db(None)
        self.welcome.refresh()
        self.tabs.addTab(self.welcome, "Welcome")
        self.setWindowTitle(APP_NAME)
        self._update_actions()

    def configure_project(self):
        cfg = self.db.config()
        dlg = ProjectDialog(self, cfg, self.db.path, new=False)
        if dlg.exec():
            cfg.update(dlg.result_config())
            self.db.set_config(cfg)
            self.setWindowTitle(f"{cfg.get('name')} — {APP_NAME}")
            self.analyze(False)

    def reload_views(self):
        db = self.db
        db.invalidate()
        self.project_tree.set_db(db)
        self.entity_filter.set_db(db)
        names = sorted({e.longname for e in db.entities(include_locals=False) if not e.is_unresolved})
        comp = QCompleter(names, self.quick)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setMaxVisibleItems(20)
        comp.activated.connect(lambda _: QTimer.singleShot(0, self._quick_find))
        self.quick.setCompleter(comp)
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, ProjectSummary):
                w.refresh()
        if self.current_entity:
            self.show_entity(self.current_entity, record=False)

    # ---- background tasks -----------------------------------------------------------------------------
    def run_task(self, label, fn, on_done):
        if self.worker is not None:
            QMessageBox.information(self, APP_NAME, "Another task is running.")
            return
        self.worker = Worker(fn, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(lambda res: self._task_finished(on_done, res))
        self.worker.failed.connect(self._task_failed)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_btn.show()
        self.status_label.setText(label)
        self._update_actions()
        self.worker.start()

    def _on_progress(self, stage, i, n, msg):
        if n > 1:
            self.progress.setRange(0, n)
            self.progress.setValue(i)
        else:
            self.progress.setRange(0, 0)
        self.status_label.setText(f"{stage}: {msg}")

    def _finish_worker(self):
        w = self.worker
        self.worker = None
        if w is not None:
            w.wait()
            w.deleteLater()
        self.progress.hide()
        self.cancel_btn.hide()
        self._update_actions()

    def _task_finished(self, on_done, res):
        self._finish_worker()
        on_done(res)

    def _task_failed(self, msg):
        self._finish_worker()
        if "Cancelled" in msg.split(":")[0]:
            self.status_label.setText("Cancelled")
            if self.db is not None:
                self.reload_views()
            return
        self.status_label.setText("Failed")
        QMessageBox.critical(self, APP_NAME, msg[:4000])

    def analyze(self, full=False):
        if self.db is None:
            return
        path = self.db.path

        def job(progress, cancelled):
            from ..core.analyzer import Analyzer
            db = Database(path)
            try:
                return Analyzer(db, progress, cancelled).analyze(full=full)
            finally:
                db.close()

        def done(st):
            self.reload_views()
            self.status_label.setText(f"Analyzed {st['files']} files ({st['parsed']} parsed, {st['errors']} errors) "
                                      f"in {st['seconds']} s")
            if not any(isinstance(self.tabs.widget(i), ProjectSummary) for i in range(self.tabs.count())):
                self.show_overview()
        self.run_task("Analyzing…", job, done)

    # ---- tabs -------------------------------------------------------------------------------------------
    def add_tab(self, w, title=None, connect=False):
        if self.welcome is not None and self.tabs.indexOf(self.welcome) >= 0 and self.db is not None:
            self.tabs.removeTab(self.tabs.indexOf(self.welcome))
        if connect:
            for sig, slot in (("entitySelected", self.show_entity), ("entityActivated", self.activate_entity),
                              ("locationActivated", self.open_location)):
                if hasattr(w, sig):
                    getattr(w, sig).connect(slot)
        idx = self.tabs.addTab(w, title or getattr(w, "title", "View"))
        self.tabs.setTabToolTip(idx, title or getattr(w, "title", ""))
        self.tabs.setCurrentIndex(idx)
        return idx

    def close_tab(self, idx):
        w = self.tabs.widget(idx)
        if isinstance(w, EditorPane) and w.editor.document().isModified():
            r = QMessageBox.question(self, "Unsaved changes", f"Save changes to {w.title}?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Save:
                w.editor.save()
        self.tabs.removeTab(idx)
        if w is not self.welcome:
            w.deleteLater()

    def _confirm_unsaved(self):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorPane) and w.editor.document().isModified():
                r = QMessageBox.question(self, "Unsaved changes", "Some files have unsaved changes. Save them?",
                                         QMessageBox.SaveAll | QMessageBox.Discard | QMessageBox.Cancel)
                if r == QMessageBox.Cancel:
                    return False
                if r == QMessageBox.SaveAll:
                    for j in range(self.tabs.count()):
                        x = self.tabs.widget(j)
                        if isinstance(x, EditorPane) and x.editor.document().isModified():
                            x.editor.save()
                return True
        return True

    def save_current(self):
        w = self.tabs.currentWidget()
        if isinstance(w, EditorPane):
            w.editor.save()
            self.status_label.setText(f"Saved {w.editor.path} — press F5 to re-analyze")

    def show_overview(self):
        for i in range(self.tabs.count()):
            if isinstance(self.tabs.widget(i), ProjectSummary):
                self.tabs.setCurrentIndex(i)
                self.tabs.widget(i).refresh()
                return
        self.add_tab(ProjectSummary(self.db), connect=True)

    # ---- navigation --------------------------------------------------------------------------------------
    def editor_for(self, file_id, create=True):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorPane) and w.editor.file_id == file_id:
                return i, w
        if not create:
            return -1, None
        f = self.db.file(file_id)
        if f is None or not os.path.exists(f.path):
            QMessageBox.warning(self, APP_NAME, "Source file not found." + (f"\n{f.path}" if f else ""))
            return -1, None
        pane = EditorPane(f.path, file_id, f.lang)
        ed = pane.editor
        ed.entityClicked.connect(self._editor_click)
        ed.entityJump.connect(self._editor_jump)
        ed.contextRequested.connect(self._editor_menu)
        ed.modifiedChanged.connect(lambda mod, p=pane: self._mark_modified(p, mod))
        idx = self.add_tab(pane, pane.title)
        self.tabs.setTabToolTip(idx, f.path)
        return idx, pane

    def _mark_modified(self, pane, mod):
        i = self.tabs.indexOf(pane)
        if i >= 0:
            self.tabs.setTabText(i, pane.title + (" *" if mod else ""))

    def _current_location(self):
        w = self.tabs.currentWidget()
        if isinstance(w, EditorPane):
            line, col = w.editor.cursor_location()
            return (w.editor.file_id, line, col)
        return None

    def open_location(self, file_id, line=1, col=1, record=True):
        if self.db is None or not file_id:
            return
        if record:
            cur = self._current_location()
            if cur and cur[:2] != (file_id, line):
                self.nav_back.append(cur)
                self.nav_fwd.clear()
        idx, pane = self.editor_for(file_id)
        if pane is None:
            return
        self.tabs.setCurrentIndex(idx)
        pane.editor.goto_line(line, col)
        self._highlight_occurrences(pane)

    def go_back(self):
        if self.nav_back:
            cur = self._current_location()
            if cur:
                self.nav_fwd.append(cur)
            self.open_location(*self.nav_back.pop(), record=False)

    def go_forward(self):
        if self.nav_fwd:
            cur = self._current_location()
            if cur:
                self.nav_back.append(cur)
            self.open_location(*self.nav_fwd.pop(), record=False)

    def activate_entity(self, eid):
        e = self.db.entity(eid) if self.db else None
        if e is None:
            return
        self.show_entity(eid)
        if e.file_id:
            self.open_location(e.file_id, e.line, e.col)

    def show_entity(self, eid, record=True):
        if self.db is None:
            return
        e = self.db.entity(eid)
        if e is None:
            return
        if record and self.current_entity and self.current_entity != e.id:
            self.info_back.append(self.current_entity)
            self.info_fwd.clear()
        self.current_entity = e.id
        self.info.show_entity(e.id)
        self.dock_info.raise_()
        w = self.tabs.currentWidget()
        if isinstance(w, EditorPane):
            self._highlight_occurrences(w)

    def _info_back(self):
        if self.info_back:
            self.info_fwd.append(self.current_entity)
            self.show_entity(self.info_back.pop(), record=False)

    def _info_fwd(self):
        if self.info_fwd:
            self.info_back.append(self.current_entity)
            self.show_entity(self.info_fwd.pop(), record=False)

    def _highlight_occurrences(self, pane):
        if not self.current_entity or self.db is None:
            pane.editor.set_occurrences([])
            return
        e = self.db.entity(self.current_entity)
        fid = pane.editor.file_id
        occ = []
        if e.file_id == fid and e.kind != M.FILE:
            occ.append((e.line, e.col, len(e.name)))
        for d in self.db.declarations(e.id):
            if d.file_id == fid:
                occ.append((d.line, d.col, len(d.name)))
        for r in self.db.refs_to(e.id):
            if r.file_id == fid and r.kind not in (M.INCLUDE, M.IMPORT):
                occ.append((r.line, r.col, len(r.name)))
        pane.editor.set_occurrences(occ)

    def _editor_click(self, fid, line, col):
        e = self.db.entity_at(fid, line, col) if self.db else None
        if e is not None:
            self.show_entity(e.id)

    def _editor_jump(self, fid, line, col):
        e = self.db.entity_at(fid, line, col) if self.db else None
        if e is None:
            self.status_label.setText("No entity at cursor")
            return
        if e.file_id:
            self.activate_entity(e.id)
        else:
            self.show_entity(e.id)
            self.status_label.setText(f"{e.longname} is not defined in the project ({e.kind})")

    def _editor_menu(self, fid, line, col, gpos):
        w = self.tabs.currentWidget()
        menu = w.editor.standard_menu() if isinstance(w, EditorPane) else QMenu(self)
        e = self.db.entity_at(fid, line, col) if self.db else None
        scope = self.db.scope_at(fid, line) if self.db else None
        first = menu.actions()[0] if menu.actions() else None
        extra = []
        if e is not None:
            extra.append(self._entity_submenu(menu, e, f"{e.name} ({e.kind})"))
        if scope is not None and (e is None or scope.id != e.id):
            extra.append(self._entity_submenu(menu, scope, f"Enclosing {scope.kind.lower()}: {scope.name}"))
        fe = self.db.file_entity(fid) if self.db else None
        if fe is not None:
            extra.append(self._entity_submenu(menu, fe, f"File: {fe.name}"))
        for sub in extra:
            menu.insertMenu(first, sub)
        if first is not None:
            menu.insertSeparator(first)
        menu.exec(gpos)

    def _entity_submenu(self, parent, e, title):
        m = QMenu(title, parent)
        self._fill_entity_menu(m, e)
        return m

    def _fill_entity_menu(self, m, e):
        m.addAction("View Information", lambda: self.show_entity(e.id))
        if e.file_id:
            m.addAction("Go to Definition", lambda: self.activate_entity(e.id))
        decls = self.db.declarations(e.id)
        for d in decls[:5]:
            m.addAction(f"Go to Declaration ({self.db.location(d)})",
                        lambda d=d: self.open_location(d.file_id, d.line, d.col))
        m.addSeparator()
        g = m.addMenu("Graphs")
        for kind in graphs_for(e):
            g.addAction(GRAPH_TYPES[kind], lambda k=kind: self.open_graph(k, e.id))
        m.addSeparator()
        m.addAction("Find in Files", lambda: self.find_in_files(e.name))
        m.addAction("Copy Qualified Name", lambda: QApplication.clipboard().setText(e.longname))

    def entity_menu(self, eid, gpos):
        e = self.db.entity(eid)
        if e is None:
            return
        m = QMenu(self)
        self._fill_entity_menu(m, e)
        m.exec(gpos)

    def _info_graph_menu(self):
        if not self.current_entity:
            return
        e = self.db.entity(self.current_entity)
        m = QMenu(self)
        for kind in graphs_for(e):
            m.addAction(GRAPH_TYPES[kind], lambda k=kind: self.open_graph(k, e.id))
        m.exec(self.info_graph_btn.mapToGlobal(self.info_graph_btn.rect().bottomLeft()))

    def _fill_graph_menu(self):
        m = self.graph_menu
        m.clear()
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        if e is not None:
            m.addSection(f"{e.name}")
            for kind in graphs_for(e):
                m.addAction(GRAPH_TYPES[kind], lambda k=kind: self.open_graph(k, e.id))
            m.addSeparator()
        else:
            a = m.addAction("(select an entity for entity graphs)")
            a.setEnabled(False)
        m.addAction(self.a_arch)
        m.addAction(self.a_uml)

    def open_graph(self, kind, eid):
        if self.db is None:
            return
        e = self.db.entity(eid) if eid else None
        try:
            panel = GraphPanel(self.db, kind, e)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Graph", f"Unable to build graph: {exc}")
            return
        panel.entityClicked.connect(self.show_entity)
        panel.entityActivated.connect(self.activate_entity)
        panel.lineActivated.connect(lambda fid, line: self.open_location(fid, line))
        self.add_tab(panel, panel.title)

    def _quick_find(self):
        text = self.quick.text().strip()
        if not text or self.db is None:
            return
        ents = [e for e in self.db.entities(like=f"%{text}%", include_locals=False) if not e.is_unresolved]
        exact = [e for e in ents if e.longname == text] or [e for e in ents if e.name == text] or ents
        if not exact:
            self.status_label.setText(f"No entity matching '{text}'")
            return
        if len(exact) > 1 and exact[0].longname != text:
            self.entity_filter.kind_box.setCurrentIndex(0)
            self.entity_filter.filter.setText(text)
            self.dock_entities.raise_()
            self.status_label.setText(f"{len(exact)} entities match '{text}'")
            return
        self.activate_entity(exact[0].id)

    def find_in_files(self, text=None):
        self.dock_find.show()
        self.dock_find.raise_()
        if isinstance(text, str) and text:
            self.find_panel.search(text)
        else:
            w = self.tabs.currentWidget()
            if isinstance(w, EditorPane) and w.editor.textCursor().hasSelection():
                self.find_panel.query.setText(w.editor.textCursor().selectedText())
            self.find_panel.query.setFocus()
            self.find_panel.query.selectAll()

    # ---- metrics / checks / reports ----------------------------------------------------------------------
    def export_metrics(self):
        from ..core.report import metrics_csv
        path, _ = QFileDialog.getSaveFileName(self, "Export Metrics", "metrics.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(metrics_csv(self.db))
            self.status_label.setText(f"Metrics written to {path}")

    def open_codecheck(self):
        for i in range(self.tabs.count()):
            if isinstance(self.tabs.widget(i), CodeCheckPanel):
                self.tabs.setCurrentIndex(i)
                return
        panel = CodeCheckPanel(self.db)
        panel.locationActivated.connect(self.open_location)
        panel.runRequested.connect(lambda en, thr, p=panel: self._run_codecheck(p, en, thr))
        self.add_tab(panel)

    def _run_codecheck(self, panel, enabled, thresholds):
        path = self.db.path

        def job(progress, cancelled):
            from ..core.codecheck import run_codecheck
            db = Database(path)
            try:
                return run_codecheck(db, enabled, thresholds, progress)
            finally:
                db.close()
        self.run_task("Running CodeCheck…", job, lambda vs: (panel.show_results(vs),
                                                                  self.status_label.setText(f"CodeCheck: {len(vs)} violations")))

    def html_report(self):
        out = QFileDialog.getExistingDirectory(self, "Report Output Directory",
                                               os.path.dirname(self.db.path))
        if not out:
            return
        out = os.path.join(out, os.path.splitext(os.path.basename(self.db.path))[0] + "_report")
        path = self.db.path

        def job(progress, cancelled):
            from ..core.report import generate_html
            db = Database(path)
            try:
                return generate_html(db, out, progress=progress)
            finally:
                db.close()

        def done(index):
            self.status_label.setText(f"Report written to {index}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(index))
        self.run_task("Generating report…", job, done)

    # ---- misc --------------------------------------------------------------------------------------------
    def toggle_theme(self):
        name = "dark" if self.a_dark.isChecked() else "light"
        theme.apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorPane):
                w.editor.apply_theme()
            elif isinstance(w, GraphPanel):
                w.refresh()
            elif hasattr(w, "refresh"):
                w.refresh()

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>Static source code analysis: cross references, metrics, graphs, "
                          "control flow, dependency analysis, CodeCheck and reports for C, C++ and Python.</p>"
                          "<p>Parsing powered by tree-sitter. User interface built with Qt (PySide6).</p>")

    def cli_help(self):
        from ..cli import build_parser
        box = QMessageBox(self)
        box.setWindowTitle("Command Line Usage")
        box.setText("The <b>codescope</b> command (codescope-cli.exe on Windows) automates analysis, "
                    "metrics export, CodeCheck and reports, e.g. in CI pipelines.")
        box.setDetailedText(build_parser().format_help() +
                            "\nExamples:\n  codescope create proj.csdb -r src -a\n  codescope check proj.csdb "
                            "--fail-on warning\n  codescope report proj.csdb -o report\n  codescope graph proj.csdb "
                            "main -t calls -o main.svg")
        box.exec()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.endswith(".csdb"):
                self.open_project(p)
                return
            if os.path.isdir(p):
                dlg = ProjectDialog(self, {"name": os.path.basename(p), "roots": [p]},
                                    os.path.join(p, os.path.basename(p) + ".csdb"))
                if dlg.exec():
                    path = dlg.db_path.text()
                    self.close_project()
                    Database.create(path, dlg.result_config()).close()
                    self.open_project(path, analyze=True)
                return
            if self.db is not None:
                f = self.db.file_by_path(p)
                if f:
                    self.open_location(f.id, 1)

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        if not self._confirm_unsaved():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        if self.db is not None:
            self.db.close()
        super().closeEvent(e)
