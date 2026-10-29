"""Project tree, entity filter and information browser."""

from __future__ import annotations

import posixpath

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
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
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.filter)
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
        top = QHBoxLayout()
        top.addWidget(self.kind_box, 1)
        top.addWidget(self.count)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(top)
        v.addWidget(self.filter)
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
        self.view.setColumnWidth(0, 200)
        self.view.setColumnWidth(1, 90)
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
        self.setHeaderHidden(True)
        self.setColumnCount(1)
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

    def _item(self, parent, text, eid=None, file_id=None, line=None, icon_kind=None, tip=None):
        it = QTreeWidgetItem(parent, [text])
        if eid is not None:
            it.setData(0, ROLE_ID, eid)
        if file_id is not None:
            it.setData(0, ROLE_FILE, file_id)
            it.setData(0, ROLE_LINE, line or 1)
        if icon_kind:
            it.setIcon(0, kind_icon(icon_kind))
        if tip:
            it.setToolTip(0, tip)
        return it

    def _loc(self, file_id, line):
        f = self.db.file(file_id) if file_id else None
        return f"{f.relpath}({line})" if f else ""

    def show_entity(self, eid):
        if self.db is None:
            return
        e = self.db.entity(eid)
        if e is None:
            return
        self.entity = e
        self.clear()
        db = self.db
        title = self._item(self, e.longname, e.id, e.file_id, e.line, e.kind)
        f = title.font(0)
        f.setBold(True)
        title.setFont(0, f)
        self._item(self, f"Kind: {e.kind}")
        if e.lang:
            self._item(self, f"Language: {display_name(e.lang)}")
        if e.signature:
            self._item(self, f"Signature: {e.signature}", tip=e.signature)
        if e.type:
            self._item(self, f"Type: {e.type}")
        if e.modifiers:
            self._item(self, f"Modifiers: {e.modifiers}")
        if e.file_id:
            self._item(self, f"Defined in: {self._loc(e.file_id, e.line)}", file_id=e.file_id, line=e.line)
        for d in db.declarations(e.id):
            self._item(self, f"Declared in: {self._loc(d.file_id, d.line)}", file_id=d.file_id, line=d.line)
        parent = db.parent(e)
        if parent is not None and parent.kind != M.FILE:
            self._item(self, f"Parent: {parent.longname}", parent.id, icon_kind=parent.kind)
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
        for label, ents in sections:
            sec = self._item(self, f"{label.replace('&&', '&')} ({len(ents)})")
            sec.setData(0, ROLE_LOADED, ents)
            sec.addChild(QTreeWidgetItem(["…"]))
            if len(ents) <= 20:
                sec.setExpanded(True)
        refs = db.refs_to(e.id) + db.refs_from(e.id, resolved=False)
        if refs:
            sec = self._item(self, f"References ({len(refs)})")
            sec.setData(0, ROLE_LOADED, ("refs", refs))
            sec.addChild(QTreeWidgetItem(["…"]))
        m = db.metrics(e.id)
        if m:
            sec = self._item(self, "Metrics")
            for k in sorted(m):
                self._item(sec, f"{k}: {fmt_value(m[k])}", tip=METRIC_DESCRIPTIONS.get(k, ""))
            sec.setExpanded(e.kind in M.FUNCTION_KINDS)
        title.setExpanded(True)

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
                    text = f"{r.kind} by {other.longname if other else '?'}  {self._loc(r.file_id, r.line)}"
                else:
                    text = f"{r.kind}s {r.name}{' (unresolved)' if r.dst_id is None else ''}  {self._loc(r.file_id, r.line)}"
                self._item(item, text, file_id=r.file_id, line=r.line)
            return
        for x in data[: self.MAX_ITEMS]:
            loc = self._loc(x.file_id, x.line) if x.file_id else "unresolved"
            it = self._item(item, f"{x.name if x.kind != M.FILE else x.longname}   {loc}", x.id, x.file_id, x.line,
                            x.kind, tip=f"{x.kind}: {x.longname}")
            del it
        if len(data) > self.MAX_ITEMS:
            self._item(item, f"… {len(data) - self.MAX_ITEMS} more")

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
