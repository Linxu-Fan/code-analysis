"""Project tree, entity filter and information browser."""

from __future__ import annotations

import posixpath

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QTreeView,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import model as M
from ..core.languages import display_name
from ..core.metrics import METRIC_DESCRIPTIONS
from .common import KIND_GROUPS, fmt_value, kind_icon

ROLE_ID = Qt.UserRole + 1
ROLE_FILE = Qt.UserRole + 2
ROLE_LINE = Qt.UserRole + 3
ROLE_LOADED = Qt.UserRole + 4


class ProjectTree(QWidget):
    """Directory tree of analyzed files; files expand to an outline of their entities."""
    entitySelected = Signal(int)
    entityActivated = Signal(int)
    contextRequested = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.filter = QLineEdit(placeholderText="Filter files…", clearButtonEnabled=True)
        self.view = QTreeView()
        self.view.setHeaderHidden(True)
        self.view.setUniformRowHeights(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.view.setModel(self.proxy)
        self.view.setFrameShape(QTreeView.NoFrame)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(6)
        box = QHBoxLayout()
        box.setContentsMargins(6, 0, 6, 0)
        box.addWidget(self.filter)
        v.addLayout(box)
        v.addWidget(self.view)
        self.filter.textChanged.connect(self._filter)
        self.view.expanded.connect(self._expand)
        self.view.clicked.connect(lambda ix: self._emit(ix, self.entitySelected))
        self.view.doubleClicked.connect(lambda ix: self._emit(ix, self.entityActivated))
        self.view.activated.connect(lambda ix: self._emit(ix, self.entityActivated))
        self.view.customContextMenuRequested.connect(self._menu)

    def _filter(self, text):
        self.proxy.setFilterFixedString(text)
        if text:
            self.view.expandToDepth(3)

    def _emit(self, ix, sig):
        eid = ix.data(ROLE_ID)
        if eid:
            sig.emit(eid)

    def _menu(self, pos):
        ix = self.view.indexAt(pos)
        eid = ix.data(ROLE_ID)
        if eid:
            self.contextRequested.emit(eid, self.view.viewport().mapToGlobal(pos))

    def set_db(self, db):
        self.db = db
        self.model.clear()
        if db is None:
            return
        dirs = {}
        root = self.model.invisibleRootItem()
        icon_dir = self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon)

        def dir_item(path):
            if not path:
                return root
            if path in dirs:
                return dirs[path]
            parent = dir_item(posixpath.dirname(path))
            it = QStandardItem(icon_dir, posixpath.basename(path))
            it.setEditable(False)
            parent.appendRow(it)
            dirs[path] = it
            return it

        for f in db.files():
            parent = dir_item(posixpath.dirname(f.relpath))
            it = QStandardItem(kind_icon(M.FILE), posixpath.basename(f.relpath))
            it.setEditable(False)
            it.setData(f.entity_id, ROLE_ID)
            it.setData(f.id, ROLE_FILE)
            it.setToolTip(f"{f.path}\n{display_name(f.lang)}" + (f"\nParse errors: {f.parse_errors}" if f.parse_errors else ""))
            placeholder = QStandardItem("…")
            it.appendRow(placeholder)
            parent.appendRow(it)
        self.proxy.sort(0)
        self.view.expandToDepth(0 if len(dirs) > 6 else 1)

    def _expand(self, pix):
        ix = self.proxy.mapToSource(pix)
        it = self.model.itemFromIndex(ix)
        if it is None or it.data(ROLE_FILE) is None or it.data(ROLE_LOADED):
            return
        it.setData(True, ROLE_LOADED)
        it.removeRows(0, it.rowCount())
        fid = it.data(ROLE_FILE)
        items = {}
        file_ent = it.data(ROLE_ID)
        for e in self.db.file_outline(fid):
            if e.kind == M.FILE:
                continue
            parent = items.get(e.parent_id, it)
            child = QStandardItem(kind_icon(e.kind), e.name)
            child.setEditable(False)
            child.setData(e.id, ROLE_ID)
            child.setToolTip(f"{e.kind}: {e.longname}")
            parent.appendRow(child)
            items[e.id] = child
        del file_ent


class _EntityModel(QStandardItemModel):
    pass


class EntityFilter(QWidget):
    """Understand-style entity filter: pick a kind group, type to filter."""
    entitySelected = Signal(int)
    entityActivated = Signal(int)
    contextRequested = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.kind_box = QComboBox()
        for label, kinds in KIND_GROUPS:
            self.kind_box.addItem(label.replace("&&", "&"), kinds)
        self.kind_box.setCurrentIndex(3)
        self.filter = QLineEdit(placeholderText="Filter (wildcards * ?)…", clearButtonEnabled=True)
        self.count = QLabel()
        self.view = QTreeView()
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        self.view.setSortingEnabled(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.model = QStandardItemModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.view.setModel(self.proxy)
        self.view.setFrameShape(QTreeView.NoFrame)
        self.count.setObjectName("Muted")
        top = QHBoxLayout()
        top.setContentsMargins(6, 0, 6, 0)
        top.addWidget(self.kind_box, 1)
        top.addWidget(self.count)
        flt = QHBoxLayout()
        flt.setContentsMargins(6, 0, 6, 0)
        flt.addWidget(self.filter)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(6)
        v.addLayout(top)
        v.addLayout(flt)
        v.addWidget(self.view)
        self._timer = QTimer(self, singleShot=True, interval=150, timeout=self._apply_filter)
        self.filter.textChanged.connect(self._timer.start)
        self.kind_box.currentIndexChanged.connect(self.reload)
        self.view.clicked.connect(lambda ix: self._emit(ix, self.entitySelected))
        self.view.doubleClicked.connect(lambda ix: self._emit(ix, self.entityActivated))
        self.view.activated.connect(lambda ix: self._emit(ix, self.entityActivated))
        self.view.customContextMenuRequested.connect(self._menu)

    def _emit(self, ix, sig):
        eid = self.proxy.index(ix.row(), 0, ix.parent()).data(ROLE_ID)
        if eid:
            sig.emit(eid)

    def _menu(self, pos):
        ix = self.view.indexAt(pos)
        if ix.isValid():
            eid = self.proxy.index(ix.row(), 0, ix.parent()).data(ROLE_ID)
            if eid:
                self.contextRequested.emit(eid, self.view.viewport().mapToGlobal(pos))

    def _apply_filter(self):
        text = self.filter.text().strip()
        if any(ch in text for ch in "*?"):
            self.proxy.setFilterWildcard(text)
        else:
            self.proxy.setFilterFixedString(text)
        self.count.setText(f"{self.proxy.rowCount()} ")

    def set_db(self, db):
        self.db = db
        self.reload()

    def reload(self):
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["Name", "Kind", "Location"])
        if self.db is None:
            self.count.setText("")
            return
        kinds = self.kind_box.currentData()
        include_locals = kinds is not None and M.LOCAL in kinds
        ents = self.db.entities(kinds=kinds, include_locals=include_locals)
        files = {f.id: f.relpath for f in self.db.files()}
        root = self.model.invisibleRootItem()
        rows = []
        for e in ents:
            name = QStandardItem(kind_icon(e.kind), e.longname if e.kind == M.FILE else e.name)
            name.setData(e.id, ROLE_ID)
            name.setToolTip(e.longname)
            name.setEditable(False)
            kind = QStandardItem(e.kind)
            kind.setEditable(False)
            loc = QStandardItem(f"{files.get(e.file_id, '')}:{e.line}" if e.file_id else "")
            loc.setEditable(False)
            rows.append([name, kind, loc])
        for r in rows:
            root.appendRow(r)
        self.view.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.view.setColumnWidth(0, 190)
        self.view.setColumnWidth(1, 100)
        self._apply_filter()


class InfoBrowser(QTreeWidget):
    """Hierarchical information about one entity: definition, metrics and all relationships."""
    entitySelected = Signal(int)
    entityActivated = Signal(int)
    locationActivated = Signal(int, int)
    contextRequested = Signal(int, object)

    MAX_ITEMS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.entity = None
        self.setFrameShape(QTreeWidget.NoFrame)
        self.setColumnCount(2)
        self.setHeaderLabels(["Name", "Details"])
        hdr = self.header()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        # details keep their full width; a horizontal scroll bar appears when the panel is narrow
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(60)
        hdr.setSectionsMovable(False)
        hdr.setToolTip("Drag the divider to resize the columns; double-click it to fit the names")
        hdr.sectionResized.connect(self._section_resized)
        hdr.sectionHandleDoubleClicked.connect(self._fit_names)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._user_width = None          # name column width chosen by dragging the divider
        self._sizing = False
        self.setIndentation(14)
        self.setUniformRowHeights(False)
        self.setRootIsDecorated(True)
        self.setAllColumnsShowFocus(True)
        self.header().setStretchLastSection(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double)
        self.itemExpanded.connect(self._expand)
        self.customContextMenuRequested.connect(self._menu)
        self.history = []

    def set_db(self, db):
        self.db = db
        self.entity = None
        self.clear()

    def refresh_theme(self):
        if self.entity is not None:
            self.show_entity(self.entity.id)

    def _muted(self):
        from . import theme
        return QColor(theme.tokens()["muted"])

    def _item(self, parent, text, eid=None, file_id=None, line=None, icon_kind=None, tip=None, value=None):
        it = QTreeWidgetItem(parent, [text, value or ""])
        if eid is not None:
            it.setData(0, ROLE_ID, eid)
        if file_id is not None:
            it.setData(0, ROLE_FILE, file_id)
            it.setData(0, ROLE_LINE, line or 1)
        if icon_kind:
            it.setIcon(0, kind_icon(icon_kind))
        if tip:
            it.setToolTip(0, tip)
            it.setToolTip(1, tip)
        it.setForeground(1, self._muted())
        return it

    def _prop(self, key, value, **kw):
        it = self._item(self, key, value=value, **kw)
        it.setForeground(0, self._muted())
        it.setForeground(1, QColor(self.palette().color(self.palette().ColorRole.Text)))
        it.setToolTip(1, value)
        return it

    def _loc(self, file_id, line):
        f = self.db.file(file_id) if file_id else None
        return f"{f.relpath}:{line}" if f else ""

    def show_entity(self, eid):
        if self.db is None:
            return
        e = self.db.entity(eid)
        if e is None:
            return
        self.entity = e
        self.setUpdatesEnabled(False)
        self.clear()
        db = self.db
        title = self._item(self, e.longname if e.kind == M.FILE else e.name, e.id, e.file_id, e.line, e.kind)
        f = title.font(0)
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.12)
        title.setFont(0, f)
        title.setFirstColumnSpanned(True)
        title.setToolTip(0, e.longname)
        if e.longname != title.text(0):
            sub = self._item(self, e.longname)
            sub.setFirstColumnSpanned(True)
            sub.setForeground(0, self._muted())
        self._prop("Kind", e.kind)
        if e.lang:
            self._prop("Language", display_name(e.lang))
        if e.signature:
            self._prop("Signature", e.signature, tip=e.signature)
        if e.type:
            self._prop("Type", e.type)
        if M.shown_modifiers(e.modifiers):
            self._prop("Modifiers", M.shown_modifiers(e.modifiers))
        if e.file_id:
            self._prop("Defined in", self._loc(e.file_id, e.line), file_id=e.file_id, line=e.line)
        for d in db.declarations(e.id):
            self._prop("Declared in", self._loc(d.file_id, d.line), file_id=d.file_id, line=d.line)
        parent = db.parent(e)
        if parent is not None and parent.kind != M.FILE:
            it = self._prop("Parent", parent.longname, eid=parent.id)
            it.setIcon(1, kind_icon(parent.kind))
        # relationships
        sections = []
        if e.kind == M.FILE:
            members = [x for x in db.file_outline(e.file_id) if x.parent_id == e.id or
                       (db.parent(x) is not None and db.parent(x).kind in (M.NAMESPACE, M.PACKAGE))]
            sections.append(("Declares", members))
        else:
            kids = db.children(e.id)
            if kids:
                sections.append(("Members" if e.kind in M.CLASS_KINDS | M.SCOPE_KINDS else "Locals && Parameters",
                                 kids))
        for kind, (fwd, back) in M.REF_LABELS.items():
            outs = db.related(e.id, (kind,))
            if outs:
                sections.append((fwd, outs))
            ins = db.related(e.id, (kind,), reverse=True)
            if ins:
                sections.append((back, ins))
        bold = QFont(self.font())
        bold.setBold(True)
        for label, ents in sections:
            sec = self._item(self, label.replace("&&", "&"), value=str(len(ents)))
            sec.setFont(0, bold)
            sec.setData(0, ROLE_LOADED, ents)
            sec.addChild(QTreeWidgetItem(["\u2026"]))
            if len(ents) <= 20:
                sec.setExpanded(True)
        refs = db.refs_to(e.id) + db.refs_from(e.id, resolved=False)
        if refs:
            sec = self._item(self, "References", value=str(len(refs)))
            sec.setFont(0, bold)
            sec.setData(0, ROLE_LOADED, ("refs", refs))
            sec.addChild(QTreeWidgetItem(["\u2026"]))
        m = db.metrics(e.id)
        if m:
            sec = self._item(self, "Metrics", value=str(len(m)))
            sec.setFont(0, bold)
            for k in sorted(m):
                it = self._item(sec, k, value=fmt_value(m[k]), tip=METRIC_DESCRIPTIONS.get(k, ""))
                it.setForeground(1, QColor(self.palette().color(self.palette().ColorRole.Text)))
            sec.setExpanded(e.kind in M.FUNCTION_KINDS)
        self._auto_width()
        self.setUpdatesEnabled(True)

    # ---- column sizing ---------------------------------------------------------------------
    def _section_resized(self, index, old, new):
        if index == 0 and not self._sizing:
            self._user_width = new

    def _set_name_width(self, w):
        self._sizing = True
        self.setColumnWidth(0, int(w))
        self._sizing = False

    def _auto_width(self):
        """Fit the name column to its content (up to about half the panel) unless the user sized it."""
        self.resizeColumnToContents(1)
        self.setColumnWidth(1, self.columnWidth(1) + 12)
        if self._user_width is not None:
            self._set_name_width(self._user_width)
            return
        self._sizing = True
        self.resizeColumnToContents(0)
        self._sizing = False
        limit = max(160, int(self.viewport().width() * 0.55))
        self._set_name_width(min(max(self.columnWidth(0), 120), limit))

    def _fit_names(self, index):
        if index == 0:
            self._sizing = True
            self.resizeColumnToContents(0)
            self._sizing = False
            self._user_width = self.columnWidth(0)

    def _expand(self, item):
        data = item.data(0, ROLE_LOADED)
        if data is None or item.data(0, ROLE_ID + 10):
            return
        item.setData(0, ROLE_ID + 10, True)
        item.takeChildren()
        if isinstance(data, tuple) and data and data[0] == "refs":
            e = self.entity
            for r in data[1][: self.MAX_ITEMS]:
                if r.dst_id == e.id:
                    other = self.db.entity(r.src_id)
                    text = f"{r.kind} by {other.name if other else '?'}"
                    icon = other.kind if other else None
                else:
                    text = f"{r.kind}s {r.name}" + (" (unresolved)" if r.dst_id is None else "")
                    icon = None
                self._item(item, text, file_id=r.file_id, line=r.line, icon_kind=icon,
                           value=self._loc(r.file_id, r.line))
            return
        for x in data[: self.MAX_ITEMS]:
            loc = self._loc(x.file_id, x.line) if x.file_id else "unresolved"
            self._item(item, x.name, x.id, x.file_id, x.line,
                       x.kind, tip=f"{x.kind}: {x.longname}", value=loc)
        if len(data) > self.MAX_ITEMS:
            self._item(item, f"\u2026 {len(data) - self.MAX_ITEMS} more")
        self._auto_width()

    def _clicked(self, item, col):
        # selecting an item only highlights it; double click navigates, context menu offers more
        pass

    def _double(self, item, col):
        fid = item.data(0, ROLE_FILE)
        if fid:
            self.locationActivated.emit(fid, item.data(0, ROLE_LINE) or 1)
        elif item.data(0, ROLE_ID):
            self.entityActivated.emit(item.data(0, ROLE_ID))

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is not None and it.data(0, ROLE_ID):
            self.contextRequested.emit(it.data(0, ROLE_ID), self.viewport().mapToGlobal(pos))
