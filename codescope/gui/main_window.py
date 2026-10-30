"""Main application window."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QCompleter, QDockWidget, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QTabWidget, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import model as M
from ..core.database import DEFAULT_CONFIG, Database
from ..core.graphs import GRAPH_TYPES, graphs_for
from . import theme
from .browsers import EntityFilter, InfoBrowser, ProjectTree
from .code_editor import EditorPane
from .common import Worker, app_icon, kind_icon
from .graph_view import GraphPanel
from .metrics_view import DependencyMatrix, MetricsBrowser, ProjectSummary, TreemapView
from .panels import CodeCheckPanel, FindInFiles, ProjectDialog

MAX_RECENT = 10


class WelcomePage(QWidget):
    """Start page: create / open a project and reopen recent ones."""

    def __init__(self, win):
        super().__init__(objectName="Page")
        self.win = win
        self.title = "Start Page"
        outer = QHBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.setSpacing(48)
        left = QVBoxLayout()
        left.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(56, 56))
        head = QHBoxLayout()
        head.addWidget(logo)
        titles = QVBoxLayout()
        titles.addWidget(QLabel(APP_NAME, objectName="Hero"))
        titles.addWidget(QLabel(f"Code comprehension for C, C++ and Python \u00b7 version {__version__}",
                                objectName="Muted"))
        head.addLayout(titles)
        head.addStretch(1)
        left.addLayout(head)
        left.addSpacing(18)
        left.addWidget(QLabel("START", objectName="Section"))
        for text, sub, icon_name, slot in (
                ("New Project\u2026", "Analyze a source tree", "new", win.new_project),
                ("Open Project\u2026", "Open an existing .csdb project", "open", win.open_project_dialog)):
            b = QPushButton(f"{text}\n{sub}", objectName="Tile")
            b.setIcon(theme.icon(icon_name))
            b.setIconSize(b.iconSize() * 1.6)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            b.setMinimumHeight(58)
            left.addWidget(b)
        left.addSpacing(18)
        left.addWidget(QLabel("RECENT PROJECTS", objectName="Section"))
        self.recent = QListWidget(objectName="Recent")
        self.recent.setFrameShape(QListWidget.NoFrame)
        self.recent.itemActivated.connect(self._open_recent)
        self.recent.itemClicked.connect(self._open_recent)
        self.recent.setCursor(Qt.PointingHandCursor)
        left.addWidget(self.recent, 1)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(QLabel("GETTING STARTED", objectName="Section"))
        tips = QLabel(
            "<p><b>Navigate</b><br>Ctrl+click or F12 jumps to a definition; Alt+\u2190 / Alt+\u2192 goes back "
            "and forward.</p>"
            "<p><b>Understand relationships</b><br>Select any entity to see its callers, callees, members, base "
            "classes and references in the Information browser.</p>"
            "<p><b>Visualize</b><br>Right-click an entity for call trees, butterfly, inheritance, UML and control "
            "flow graphs. Graphs \u2192 Architecture shows dependencies between folders.</p>"
            "<p><b>Measure and check</b><br>Metrics, treemaps and a dependency matrix reveal hot spots; CodeCheck "
            "finds dead code, complexity and risky constructs.</p>"
            "<p><b>Search</b><br>Ctrl+E finds an entity by name, Ctrl+Shift+F searches all files.</p>")
        tips.setWordWrap(True)
        tips.setObjectName("Muted")
        tips.setTextFormat(Qt.RichText)
        tips.setMaximumWidth(420)
        right.addWidget(tips)
        right.addStretch(1)
        outer.addLayout(left, 3)
        outer.addLayout(right, 2)
        self.refresh()

    def refresh(self):
        self.recent.clear()
        for p in self.win.recent_projects():
            it = QListWidgetItem(theme.icon("project"), f"{os.path.splitext(os.path.basename(p))[0]}\n{p}")
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.recent.addItem(it)
        if not self.recent.count():
            it = QListWidgetItem("No recent projects yet")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
        self.recent.setIconSize(self.recent.iconSize().expandedTo(QSize(22, 22)))

    def _open_recent(self, item):
        p = item.data(Qt.UserRole)
        if p:
            self.win.open_project(p)


class PanelHeader(QWidget):
    """Slim tool strip used at the top of dock panels."""

    def __init__(self, parent=None):
        super().__init__(parent, objectName="PanelBar")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 3, 6, 3)
        self.lay.setSpacing(2)

    def button(self, icon_name, tip, slot, text=None):
        b = QToolButton()
        b.setIcon(theme.icon(icon_name))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setProperty("iconName", icon_name)
        if text:
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        if slot:
            b.clicked.connect(slot)
        self.lay.addWidget(b)
        return b


class SizedBox(QWidget):
    """Container with a preferred width, so docks open at a useful size."""

    def __init__(self, width, parent=None):
        super().__init__(parent)
        self._w = width

    def sizeHint(self):
        return QSize(self._w, 600)


class MainWindow(QMainWindow):
    STATE_KEY = "windowState/v2"

    def __init__(self):
        super().__init__()
        self.settings = QSettings("CodeScope", "CodeScope")
        self.db: Database | None = None
        self.worker = None
        self.nav_back, self.nav_fwd = [], []
        self.info_back, self.info_fwd = [], []
        self.current_entity = None
        self._themed = []                # (object, icon name) pairs re-coloured on theme change
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setAcceptDrops(True)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks)
        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value(self.STATE_KEY)
        self._default_layout = state is None or not self.restoreState(state)
        self._update_actions()

    def showEvent(self, e):
        super().showEvent(e)
        if self._default_layout:
            self._default_layout = False
            QTimer.singleShot(0, lambda: self.resizeDocks([self.dock_project, self.dock_info], [290, 380],
                                                          Qt.Horizontal))

    # ---- construction -------------------------------------------------------------------------
    def _build_central(self):
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)
        self.welcome = WelcomePage(self)
        self.tabs.addTab(self.welcome, app_icon(), "Start Page")

    @staticmethod
    def _sized(widget, width):
        box = SizedBox(width)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        return box

    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable |
                      QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        self.project_tree = ProjectTree()
        self.entity_filter = EntityFilter()
        self.info = InfoBrowser()
        self.find_panel = FindInFiles()
        self.dock_project = self._dock("Project Browser", self._sized(self.project_tree, 290), Qt.LeftDockWidgetArea,
                                       "dock_project")
        self.dock_entities = self._dock("Entity Filter", self._sized(self.entity_filter, 290), Qt.LeftDockWidgetArea,
                                        "dock_entities")
        self.tabifyDockWidget(self.dock_project, self.dock_entities)
        self.dock_project.raise_()
        info_box = SizedBox(380)
        iv = QVBoxLayout(info_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)
        bar = PanelHeader()
        self.info_back_btn = bar.button("back", "Previous entity", self._info_back)
        self.info_fwd_btn = bar.button("forward", "Next entity", self._info_fwd)
        bar.lay.addStretch(1)
        self.info_graph_btn = bar.button("graph", "Graphs of this entity", self._info_graph_menu, "Graphs")
        self._themed += [(self.info_back_btn, "back"), (self.info_fwd_btn, "forward"), (self.info_graph_btn, "graph")]
        iv.addWidget(bar)
        iv.addWidget(self.info)
        self.dock_info = self._dock("Information Browser", info_box, Qt.RightDockWidgetArea, "dock_info")
        self.dock_find = self._dock("Find in Files", self.find_panel, Qt.BottomDockWidgetArea, "dock_find")
        from .ai_panel import AIPanel
        self.ai_panel = AIPanel()
        self.ai_panel.locationActivated.connect(lambda fid, line: self.open_location(fid, line))
        self.dock_ai = self._dock("AI Assistant", self._sized(self.ai_panel, 440), Qt.RightDockWidgetArea, "dock_ai")
        self.tabifyDockWidget(self.dock_info, self.dock_ai)
        self.dock_info.raise_()
        self.dock_ai.hide()
        self.dock_find.hide()
        for w in (self.project_tree, self.entity_filter, self.info):
            w.entitySelected.connect(self.show_entity)
            w.entityActivated.connect(self.activate_entity)
            w.contextRequested.connect(self.entity_menu)
        self.info.locationActivated.connect(self.open_location)
        self.find_panel.locationActivated.connect(self.open_location)

    def _act(self, text, slot, shortcut=None, tip=None, icon=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        tip = tip or text.replace("&", "").rstrip("\u2026")
        a.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
        a.setStatusTip(tip)
        if icon:
            a.setIcon(theme.icon(icon))
            self._themed.append((a, icon))
        return a

    def _build_actions(self):
        self.a_new = self._act("&New Project\u2026", self.new_project, "Ctrl+N", icon="new")
        self.a_open = self._act("&Open Project\u2026", self.open_project_dialog, "Ctrl+O", icon="open")
        self.a_close = self._act("&Close Project", self.close_project)
        self.a_save = self._act("&Save File", self.save_current, "Ctrl+S", icon="save")
        self.a_exit = self._act("E&xit", self.close, "Ctrl+Q")
        self.a_config = self._act("&Configure Project\u2026", self.configure_project, icon="settings")
        self.a_analyze = self._act("&Analyze Changed Files", lambda: self.analyze(False), "F5", icon="analyze")
        self.a_full = self._act("Analyze &All Files", lambda: self.analyze(True), "Ctrl+F5")
        self.a_overview = self._act("Project &Overview", self.show_overview, icon="info")
        self.a_find = self._act("&Find in Files\u2026", self.find_in_files, "Ctrl+Shift+F", icon="find_files")
        self.a_find_entity = self._act("Find &Entity\u2026", lambda: (self.quick.setFocus(), self.quick.selectAll()),
                                       "Ctrl+E", icon="search")
        self.a_back = self._act("&Back", self.go_back, "Alt+Left", icon="back")
        self.a_fwd = self._act("&Forward", self.go_forward, "Alt+Right", icon="forward")
        self.a_metrics = self._act("&Metrics Browser", lambda: self.add_tab(MetricsBrowser(self.db), connect=True,
                                                                          icon="metrics"), icon="metrics")
        self.a_treemap = self._act("Metrics &Treemap", lambda: self.add_tab(TreemapView(self.db), connect=True,
                                                                          icon="treemap"), icon="treemap")
        self.a_dsm = self._act("&Dependency Matrix", lambda: self.add_tab(DependencyMatrix(self.db), connect=True,
                                                                        icon="dsm"), icon="dsm")
        self.a_export_metrics = self._act("&Export Metrics (CSV)\u2026", self.export_metrics)
        self.a_arch = self._act("&Architecture Graph", lambda: self.open_graph("architecture", None),
                                icon="architecture")
        self.a_uml = self._act("Project &UML Class Diagram", lambda: self.open_graph("uml", None), icon="uml")
        self.a_calltree = self._act("Call &Tree\u2026", lambda: self.open_call_tree(self._current_function()),
                                    "Ctrl+Shift+T", tip="Callers and/or callees of a function as a tree", icon="calls")
        self.a_classtree = self._act("C&lass Tree\u2026", lambda: self.open_class_tree(self._current_class()),
                                     "Ctrl+Shift+H", tip="Base and/or derived classes of a class as a tree",
                                     icon="architecture")
        self.a_modtree = self._act("&Modification Tree\u2026", lambda: self.open_modify_tree(self._current_modify_target()),
                                   "Ctrl+Shift+M", tip="Where and in which order a struct or one of its members "
                                   "is modified", icon="modify")
        self.a_codecheck = self._act("&CodeCheck\u2026", self.open_codecheck, "Ctrl+Shift+K", icon="check")
        self.a_report = self._act("&HTML Report\u2026", self.html_report, icon="report")
        self.a_ai = self._act("AI &Assistant", self.show_ai, "Ctrl+Shift+A",
                              tip="Ask a local AI model about the code", icon="ai")
        self.a_ai_entity = self._act("Ask AI About Current Entity", self._ask_ai_current)
        self.a_ai_settings = self._act("AI &Settings\u2026", lambda: (self.show_ai(), self.ai_panel.edit_settings()))
        self.a_ai_setup = self._act("Set &Up Local Model\u2026", lambda: (self.show_ai(), self.ai_panel.setup()))
        self.a_ai_help = self._act("AI Setup &Guide", self.ai_setup_guide)
        self.a_dark = self._act("&Dark Theme", self.toggle_theme, icon="theme")
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
        self.graph_menu.aboutToShow.connect(lambda: self._fill_graph_menu(self.graph_menu))
        m = mb.addMenu("&Metrics")
        for a in (self.a_metrics, self.a_treemap, self.a_dsm, None, self.a_export_metrics):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Checks")
        m.addAction(self.a_codecheck)
        m = mb.addMenu("&Reports")
        m.addAction(self.a_report)
        m = mb.addMenu("A&I")
        for a in (self.a_ai, self.a_ai_entity, None, self.a_ai_setup, self.a_ai_settings, self.a_ai_help):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&View")
        m.addAction(self.a_dark)
        sm = m.addMenu("Editor Color Scheme")
        self.scheme_actions = []
        for key, label in (("auto", "Follow Application Theme"), ("vs-light", "Visual Studio Light"),
                           ("vs-dark", "Visual Studio Dark")):
            a = sm.addAction(label, lambda k=key: self.set_editor_scheme(k))
            a.setCheckable(True)
            a.setData(key)
            a.setChecked(theme.current.get("editor_scheme", "auto") == key)
            self.scheme_actions.append(a)
        m.addAction("Editor Font\u2026", self.choose_editor_font)
        zm = m.addMenu("Editor Zoom")
        zm.addAction("Zoom In", lambda: self._editor_zoom(1), QKeySequence.ZoomIn)
        zm.addAction("Zoom Out", lambda: self._editor_zoom(-1), QKeySequence.ZoomOut)
        zm.addAction("Reset Zoom", lambda: self._editor_zoom(0), QKeySequence("Ctrl+0"))
        m.addSeparator()
        for d in (self.dock_project, self.dock_entities, self.dock_info, self.dock_find):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        m.addAction("Reset Layout", self.reset_layout)
        m = mb.addMenu("&Help")
        m.addAction(self.a_docs)
        m.addAction(self.a_about)
        self.a_dark.setChecked(theme.is_dark())

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        for a in (self.a_new, self.a_open, self.a_save, None, self.a_analyze, self.a_config, None,
                  self.a_back, self.a_fwd, None):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.quick = QLineEdit(placeholderText="Find entity\u2026   Ctrl+E", clearButtonEnabled=True)
        self.quick.setMinimumWidth(240)
        self.quick.setMaximumWidth(360)
        self.quick_icon = self.quick.addAction(theme.icon("search"), QLineEdit.LeadingPosition)
        self._themed.append((self.quick_icon, "search"))
        self.quick.returnPressed.connect(self._quick_find)
        tb.addWidget(self.quick)
        tb.addAction(self.a_find)
        tb.addSeparator()
        self.graph_btn = QToolButton()
        self.graph_btn.setIcon(theme.icon("graph"))
        self.graph_btn.setToolTip("Graphs for the selected entity")
        self.graph_btn.setPopupMode(QToolButton.InstantPopup)
        gm = QMenu(self.graph_btn)
        gm.aboutToShow.connect(lambda: self._fill_graph_menu(gm))
        self.graph_btn.setMenu(gm)
        self._themed.append((self.graph_btn, "graph"))
        tb.addWidget(self.graph_btn)
        for a in (self.a_calltree, self.a_classtree, self.a_modtree, self.a_arch, self.a_uml, None, self.a_metrics, self.a_treemap, self.a_dsm, None,
                  self.a_codecheck, self.a_report, None, self.a_ai):
            tb.addSeparator() if a is None else tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.a_dark)

    def _build_status(self):
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setMaximumHeight(14)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel())
        self.status_label = QLabel()
        self.project_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)
        self.statusBar().addPermanentWidget(self.project_label)

    def reset_layout(self):
        for d, area in ((self.dock_project, Qt.LeftDockWidgetArea), (self.dock_info, Qt.RightDockWidgetArea),
                        (self.dock_find, Qt.BottomDockWidgetArea)):
            d.setFloating(False)
            self.addDockWidget(area, d)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_entities)
        self.tabifyDockWidget(self.dock_project, self.dock_entities)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_ai)
        self.tabifyDockWidget(self.dock_info, self.dock_ai)
        for d in (self.dock_project, self.dock_entities, self.dock_info):
            d.show()
        self.dock_find.hide()
        self.dock_ai.hide()
        self.dock_project.raise_()
        self.resizeDocks([self.dock_project, self.dock_info], [300, 360], Qt.Horizontal)

    def _update_actions(self):
        has = self.db is not None
        busy = self.worker is not None
        for a in (self.a_close, self.a_config, self.a_analyze, self.a_full, self.a_overview, self.a_find,
                  self.a_metrics, self.a_treemap, self.a_dsm, self.a_export_metrics, self.a_arch, self.a_uml,
                  self.a_codecheck, self.a_report, self.a_find_entity, self.a_calltree, self.a_classtree,
                  self.a_modtree):
            a.setEnabled(has and not (busy and a in (self.a_analyze, self.a_full, self.a_config)))
        self.graph_btn.setEnabled(has)
        self.quick.setEnabled(has)
        self.a_new.setEnabled(not busy)
        self.a_open.setEnabled(not busy)
        self.project_label.setText(f"{self.db.config().get('name')}  \u00b7  {os.path.basename(self.db.path)}"
                                   if has else "")

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
        self.ai_panel.set_db(self.db)
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
        for w in (self.project_tree, self.entity_filter, self.info, self.find_panel, self.ai_panel):
            w.set_db(None)
        self.welcome.refresh()
        self.tabs.addTab(self.welcome, app_icon(), "Start Page")
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
        for ed in self._editors():
            if not ed.document().isModified():
                ed.set_semantic(self._semantic_tokens(ed.file_id), *self._type_names(refresh=True))
        self.project_tree.set_db(db)
        self.entity_filter.set_db(db)
        self.ai_panel.refresh_files()
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
    def add_tab(self, w, title=None, connect=False, icon=None):
        if self.welcome is not None and self.tabs.indexOf(self.welcome) >= 0 and self.db is not None:
            self.tabs.removeTab(self.tabs.indexOf(self.welcome))
        if connect:
            for sig, slot in (("entitySelected", self.show_entity), ("entityActivated", self.activate_entity),
                              ("locationActivated", self.open_location)):
                if hasattr(w, sig):
                    getattr(w, sig).connect(slot)
        if isinstance(icon, str):
            w.setProperty("iconName", icon)
            icon = theme.icon(icon)
        if icon is not None:
            idx = self.tabs.addTab(w, icon, title or getattr(w, "title", "View"))
        else:
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
        self.add_tab(ProjectSummary(self.db), connect=True, icon="info")

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
        ed.set_semantic(self._semantic_tokens(file_id), *self._type_names())
        idx = self.add_tab(pane, pane.title, icon=kind_icon(M.FILE))
        self.tabs.setTabToolTip(idx, f.path)
        return idx, pane

    def _semantic_tokens(self, file_id):
        """(line, col, name, kind) for every resolved reference and declaration in a file."""
        rows = self.db.conn.execute(
            "SELECT r.line, r.col, r.name, e.kind FROM refs r JOIN entities e ON e.id = r.dst_id "
            "WHERE r.file_id=? AND r.kind NOT IN ('Include', 'Import') "
            "UNION ALL SELECT line, col, name, kind FROM entities WHERE file_id=? AND kind != 'File'",
            (file_id, file_id)).fetchall()
        return rows

    def _type_names(self, refresh=False):
        """(type names, macro names) of the project: colour Foo:: scopes as types and macros purple."""
        if refresh or getattr(self, "_type_name_cache", None) is None or self._type_name_db is not self.db:
            kinds = sorted(M.CLASS_KINDS | {M.TYPEDEF, M.UNRESOLVED_TYPE})
            rows = self.db.conn.execute("SELECT DISTINCT name FROM entities WHERE kind IN (%s)" %
                                        ",".join("?" * len(kinds)), kinds).fetchall()
            macros = self.db.conn.execute("SELECT DISTINCT name FROM entities WHERE kind=?", (M.MACRO,)).fetchall()
            self._type_name_cache = (frozenset(r[0] for r in rows), frozenset(r[0] for r in macros))
            self._type_name_db = self.db
        return self._type_name_cache

    def _editors(self):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, EditorPane):
                yield w.editor

    def set_editor_scheme(self, name):
        theme.current["editor_scheme"] = name
        self.settings.setValue("editorScheme", name)
        for a in self.scheme_actions:
            a.setChecked(a.data() == name)
        for ed in self._editors():
            ed.apply_theme()

    def choose_editor_font(self):
        from PySide6.QtWidgets import QFontDialog
        from .code_editor import mono_font
        ok, font = QFontDialog.getFont(mono_font(), self, "Editor Font",
                                       QFontDialog.MonospacedFonts)
        if isinstance(ok, QFont):          # PySide returns (font, ok) or (ok, font) depending on version
            ok, font = font, ok
        if ok:
            theme.current["editor_font"] = (font.family(), font.pointSizeF())
            self.settings.setValue("editorFont", f"{font.family()}|{font.pointSizeF()}")
            for ed in self._editors():
                ed.set_editor_font(mono_font())

    def _editor_zoom(self, delta):
        w = self.tabs.currentWidget()
        if isinstance(w, EditorPane):
            if delta:
                w.editor.zoom(delta)
            else:
                w.editor.zoom_reset()

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
        sel = self._editor_selection()
        if sel is not None:
            ask = QAction(theme.icon("ai"), "Ask AI About Selection", menu)
            ask.triggered.connect(lambda: self.ask_ai_selection(sel))
            menu.insertAction(first, ask)
        for sub in extra:
            menu.insertMenu(first, sub)
        if first is not None:
            menu.insertSeparator(first)
        menu.exec(gpos)

    # ---- AI assistant -----------------------------------------------------------------------------
    def show_ai(self):
        self.dock_ai.show()
        self.dock_ai.raise_()
        self.ai_panel.input.setFocus()

    def ask_ai(self, question, context="", shown=None):
        self.show_ai()
        if self.ai_panel.busy():
            self.status_label.setText("The AI assistant is still answering the previous question.")
            return
        self.ai_panel.ask(question, context, shown)

    def ask_ai_about(self, e):
        where = f" ({self.db.location(e)})" if e.file_id else ""
        self.ask_ai(f"Explain the {e.kind.lower()} `{e.longname}`{where}: what it does, how it works and "
                    "where it is used.")

    def _ask_ai_current(self):
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        if e is None:
            self.show_ai()
            return
        self.ask_ai_about(e)

    def _editor_selection(self):
        w = self.tabs.currentWidget()
        if not isinstance(w, EditorPane) or self.db is None:
            return None
        c = w.editor.textCursor()
        if not c.hasSelection():
            return None
        doc = w.editor.document()
        first = doc.findBlock(c.selectionStart()).blockNumber() + 1
        last = doc.findBlock(c.selectionEnd()).blockNumber() + 1
        f = self.db.file(w.editor.file_id) if w.editor.file_id else None
        text = c.selectedText().replace("\u2029", "\n")
        return (f.relpath if f else os.path.basename(w.editor.path), first, last, text, f.lang if f else "cpp")

    def ask_ai_selection(self, sel):
        from .ai_panel import selection_context
        rel, first, last, text, lang = sel
        self.ask_ai("Explain this code: what it does and how it fits into the project.",
                    selection_context(rel, first, last, text, lang),
                    shown=f"Explain the selected code in {rel}:{first}-{last}.")

    def ai_setup_guide(self):
        here = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
        for base in (here, os.path.join(here, ".."), os.path.join(here, "..", "..")):
            path = os.path.normpath(os.path.join(base, "docs", "AI_SETUP.md"))
            if os.path.exists(path):
                from .ai_panel import show_markdown
                with open(path, encoding="utf-8") as fh:
                    show_markdown(self, "AI Setup Guide", fh.read())
                return
        QDesktopServices.openUrl(QUrl("https://github.com/Linxu-Fan/code-analysis/blob/main/docs/AI_SETUP.md"))

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
        if e.kind in M.FUNCTION_KINDS:
            m.addAction(theme.icon("calls"), "Call Tree\u2026", lambda: self.open_call_tree(e))
        if e.kind in M.CLASS_KINDS:
            m.addAction(theme.icon("architecture"), "Class Tree\u2026", lambda: self.open_class_tree(e))
        if e.kind in M.CLASS_KINDS or e.kind in (M.FIELD, M.PROPERTY):
            m.addAction(theme.icon("modify"), "Modification Tree\u2026", lambda: self.open_modify_tree(e))
        m.addAction(theme.icon("ai"), "Ask AI About This", lambda: self.ask_ai_about(e))
        g = m.addMenu(theme.icon("graph"), "Graphs")
        for kind in graphs_for(e):
            g.addAction(theme.icon(self.GRAPH_ICONS.get(kind, "graph")), GRAPH_TYPES[kind],
                        lambda k=kind: self.open_graph(k, e.id))
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

    def _current_function(self):
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        return e if e is not None and e.kind in M.FUNCTION_KINDS else None

    def _current_class(self):
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        if e is not None and e.kind in M.FUNCTION_KINDS | M.SCOPE_KINDS | {M.FIELD}:
            e = self.db.enclosing(e, M.CLASS_KINDS)       # a member selected: use its class
        return e if e is not None and e.kind in M.CLASS_KINDS else None

    def _current_modify_target(self):
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        if e is not None and e.kind in (M.FIELD, M.PROPERTY):
            return e
        return self._current_class()

    def open_call_tree(self, entity=None, mode="call", member=None):
        from .calltree_view import CallTreePanel
        if self.db is None:
            return
        panel = CallTreePanel(self.db, entity, mode=mode, member=member)
        icon = {"call": "calls", "class": "architecture", "modify": "modify"}[mode]
        self.add_tab(panel, panel.title, connect=True, icon=icon)
        if entity is None:
            panel.picker.setFocus()

    def open_class_tree(self, entity=None):
        self.open_call_tree(entity, mode="class")

    def open_modify_tree(self, entity=None):
        """Modification tree of a class/struct, or of one member when a field is given."""
        member = None
        if entity is not None and entity.kind in (M.FIELD, M.PROPERTY):
            member, entity = entity, self.db.enclosing(entity, M.CLASS_KINDS)
        self.open_call_tree(entity, mode="modify", member=member)

    def _info_graph_menu(self):
        if not self.current_entity:
            return
        e = self.db.entity(self.current_entity)
        m = QMenu(self)
        if e.kind in M.FUNCTION_KINDS:
            m.addAction(theme.icon("calls"), "Call Tree\u2026", lambda: self.open_call_tree(e))
        if e.kind in M.CLASS_KINDS:
            m.addAction(theme.icon("architecture"), "Class Tree\u2026", lambda: self.open_class_tree(e))
        if e.kind in M.CLASS_KINDS or e.kind in (M.FIELD, M.PROPERTY):
            m.addAction(theme.icon("modify"), "Modification Tree\u2026", lambda: self.open_modify_tree(e))
        for kind in graphs_for(e):
            m.addAction(theme.icon(self.GRAPH_ICONS.get(kind, "graph")), GRAPH_TYPES[kind],
                        lambda k=kind: self.open_graph(k, e.id))
        m.exec(self.info_graph_btn.mapToGlobal(self.info_graph_btn.rect().bottomLeft()))

    GRAPH_ICONS = {"calls": "calls", "callby": "calls", "butterfly": "graph", "control": "flow",
                   "inheritance": "architecture", "uml": "uml", "includes": "graph", "includedby": "graph",
                   "filedeps": "graph"}

    def _fill_graph_menu(self, m):
        m.clear()
        e = self.db.entity(self.current_entity) if (self.db and self.current_entity) else None
        if e is not None:
            m.addSection(f"{e.name}")
            if e.kind in M.FUNCTION_KINDS:
                m.addAction(theme.icon("calls"), "Call Tree\u2026", lambda: self.open_call_tree(e))
            if e.kind in M.CLASS_KINDS:
                m.addAction(theme.icon("architecture"), "Class Tree\u2026", lambda: self.open_class_tree(e))
            if e.kind in M.CLASS_KINDS or e.kind in (M.FIELD, M.PROPERTY):
                m.addAction(theme.icon("modify"), "Modification Tree\u2026", lambda: self.open_modify_tree(e))
            for kind in graphs_for(e):
                m.addAction(theme.icon(self.GRAPH_ICONS.get(kind, "graph")), GRAPH_TYPES[kind],
                            lambda k=kind: self.open_graph(k, e.id))
            m.addSeparator()
        else:
            a = m.addAction("(select an entity for entity graphs)")
            a.setEnabled(False)
        m.addAction(self.a_calltree)
        m.addAction(self.a_classtree)
        m.addAction(self.a_modtree)
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
        self.add_tab(panel, panel.title, icon=self.GRAPH_ICONS.get(kind, "architecture" if kind == "architecture" else "graph"))

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
        self.add_tab(panel, icon="check")

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
        for obj, icon_name in self._themed:
            obj.setIcon(theme.icon(icon_name))
        for i in range(self.tabs.count()):
            nm = self.tabs.widget(i).property("iconName")
            if nm:
                self.tabs.setTabIcon(i, theme.icon(nm))
        self.welcome.refresh()
        self.info.refresh_theme()
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
        self.settings.setValue(self.STATE_KEY, self.saveState())
        if self.db is not None:
            self.db.close()
        super().closeEvent(e)
