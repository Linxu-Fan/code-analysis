"""Metrics browser, project summary, treemap and dependency structure matrix."""

from __future__ import annotations

import math
import posixpath

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QTableView, QTableWidget, QTableWidgetItem, QToolTip,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import model as M
from ..core.graphs import directory_dependencies, file_dependencies
from ..core.languages import display_name
from ..core.metrics import METRIC_DESCRIPTIONS
from ..core.report import CLASS_METRICS, FILE_METRICS, FUNC_METRICS, metrics_csv
from . import theme
from .common import fmt_value, kind_icon

def _bar(layout):
    """Wrap a toolbar row layout in a styled panel strip."""
    w = QWidget(objectName="PanelBar")
    layout.setContentsMargins(10, 6, 10, 6)
    w.setLayout(layout)
    return w


def _page(widget):
    v = QVBoxLayout(widget)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    return v


GROUPS = [
    ("Functions", sorted(M.FUNCTION_KINDS), FUNC_METRICS + ["CountLine", "CountLineComment", "CountReturn",
                                                             "HalsteadDifficulty", "HalsteadEffort"]),
    ("Classes", sorted(M.CLASS_KINDS), CLASS_METRICS + ["CountLine", "CountDeclMethodPublic", "CountClassBase",
                                                         "MaxCyclomatic"]),
    ("Files", [M.FILE], FILE_METRICS + ["AvgCyclomatic"]),
]


class MetricsModel(QAbstractTableModel):
    def __init__(self, db, kinds, names):
        super().__init__()
        self.db = db
        self.names = names
        ents, vals = db.metric_table(kinds)
        self.rows = [e for e in ents if e.file_id is not None]
        self.vals = vals
        self.files = {f.id: f.relpath for f in db.files()}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 2 + len(self.names)

    def headerData(self, section, orient, role=Qt.DisplayRole):
        if orient != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return ["Entity", "File"][section] if section < 2 else self.names[section - 2]
        if role == Qt.ToolTipRole and section >= 2:
            return METRIC_DESCRIPTIONS.get(self.names[section - 2], "")
        return None

    def data(self, index, role=Qt.DisplayRole):
        e = self.rows[index.row()]
        c = index.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return e.longname
            if c == 1:
                return f"{self.files.get(e.file_id, '')}:{e.line}"
            return fmt_value(self.vals.get(e.id, {}).get(self.names[c - 2]))
        if role == Qt.UserRole:          # sort key
            if c < 2:
                return self.data(index, Qt.DisplayRole).lower()
            return self.vals.get(e.id, {}).get(self.names[c - 2], -1)
        if role == Qt.DecorationRole and c == 0:
            return kind_icon(e.kind)
        if role == Qt.TextAlignmentRole and c >= 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.UserRole + 1:
            return e.id
        return None


class MetricsBrowser(QWidget):
    entitySelected = Signal(int)
    entityActivated = Signal(int)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.title = "Metrics"
        self.group = QComboBox()
        for label, kinds, names in GROUPS:
            self.group.addItem(label, (kinds, names))
        self.filter = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        export = QPushButton("Export CSV…")
        self.view = QTableView()
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        self.view.verticalHeader().hide()
        self.view.setAlternatingRowColors(True)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSortRole(Qt.UserRole)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.view.setModel(self.proxy)
        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.group)
        top.addWidget(self.filter, 1)
        top.addWidget(export)
        self.view.setFrameShape(QTableView.NoFrame)
        v = _page(self)
        v.addWidget(_bar(top))
        v.addWidget(self.view)
        self.group.currentIndexChanged.connect(self.reload)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        export.clicked.connect(self.export)
        self.view.clicked.connect(lambda ix: self._emit(ix, self.entitySelected))
        self.view.doubleClicked.connect(lambda ix: self._emit(ix, self.entityActivated))
        self.reload()

    def _emit(self, ix, sig):
        eid = self.proxy.data(ix, Qt.UserRole + 1)
        if eid:
            sig.emit(eid)

    def reload(self):
        kinds, names = self.group.currentData()
        self.model = MetricsModel(self.db, kinds, names)
        self.proxy.setSourceModel(self.model)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.view.setColumnWidth(0, 320)
        if len(names):
            self.view.sortByColumn(3 if self.group.currentIndex() == 0 else 2, Qt.DescendingOrder)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Metrics", "metrics.csv", "CSV (*.csv)")
        if path:
            kinds, names = self.group.currentData()
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(metrics_csv(self.db, kinds, names))


class ProjectSummary(QScrollArea):
    """Dashboard shown after opening a project."""
    entityActivated = Signal(int)
    entitySelected = Signal(int)

    CARDS = [("Files", "Files"), ("CountLine", "Lines"), ("CountLineCode", "Code lines"),
             ("CountLineComment", "Comment lines"), ("Classes", "Classes"), ("Functions", "Functions"),
             ("MaxCyclomatic", "Max complexity"), ("AvgCyclomatic", "Avg complexity")]

    def __init__(self, db, checks=None, parent=None):
        super().__init__(parent)
        from . import charts
        self.db = db
        self.title = "Project Overview"
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        page = QWidget(objectName="Page")
        self.setWidget(page)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(32, 26, 32, 26)
        outer.setSpacing(14)
        self.name = QLabel(objectName="Hero")
        self.sub = QLabel(objectName="Muted")
        self.sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.name)
        outer.addWidget(self.sub)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.values = {}
        for i, (key, label) in enumerate(self.CARDS):
            card = QFrame(objectName="Card")
            card.setStyleSheet(f"QFrame#Card {{ border-left: 4px solid {charts.PALETTE[i % len(charts.PALETTE)]}; }}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(2)
            v = QLabel("-", objectName="CardValue")
            cl.addWidget(v)
            cl.addWidget(QLabel(label, objectName="CardLabel"))
            self.values[key] = v
            grid.addWidget(card, i // 4, i % 4)
        outer.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.sunburst = charts.SunburstChart()
        self.lines = charts.HalfDonutChart()
        self.bubbles = charts.BubbleChart()
        for chart, title, sub in ((self.sunburst, "Directory Structure", "Segment size by lines of code"),
                                  (self.lines, "Line Breakdown", "Categorization by line type"),
                                  (self.bubbles, "Most Complex Functions",
                                   "Complexity by the McCabe cyclomatic metric")):
            frame = QFrame(objectName="Card")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(12, 12, 12, 12)
            fl.setSpacing(2)
            t = QLabel(title, objectName="ChartTitle")
            t.setAlignment(Qt.AlignCenter)
            st = QLabel(sub, objectName="Muted")
            st.setAlignment(Qt.AlignCenter)
            fl.addWidget(t)
            fl.addWidget(st)
            fl.addWidget(chart, 1)
            frame.setMinimumHeight(330)
            row.addWidget(frame, 1)
        self.sunburst.fileActivated.connect(self.entityActivated)
        self.bubbles.entityActivated.connect(self.entityActivated)
        self.bubbles.entitySelected.connect(self.entitySelected)
        outer.addLayout(row)
        tables = QHBoxLayout()
        tables.setSpacing(16)
        self.complex = self._table(["Most complex functions", "Cyclomatic", "Code lines"])
        self.files = self._table(["Largest files", "Code lines", "Functions"])
        self.langs = self._table(["Language", "Files", "Code lines"])
        tables.addWidget(self.complex, 4)
        tables.addWidget(self.files, 3)
        tables.addWidget(self.langs, 2)
        for t in (self.complex, self.files, self.langs):
            t.setMinimumHeight(260)
        outer.addLayout(tables, 1)
        self.refresh()

    def _table(self, headers):
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.setUniformRowHeights(True)
        t.setAlternatingRowColors(True)
        t.setObjectName("Card")
        t.setTextElideMode(Qt.ElideMiddle)
        t.header().setStretchLastSection(False)
        t.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(headers)):
            t.header().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        t.itemDoubleClicked.connect(lambda it, col: it.data(0, Qt.UserRole) and
                                    self.entityActivated.emit(it.data(0, Qt.UserRole)))
        return t

    def _row(self, table, cols, eid=None, kind=None):
        it = QTreeWidgetItem([str(c) for c in cols])
        for c in range(1, len(cols)):
            it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        if eid:
            it.setData(0, Qt.UserRole, eid)
            it.setToolTip(0, cols[0])
        if kind:
            it.setIcon(0, kind_icon(kind))
        table.addTopLevelItem(it)

    def refresh(self):
        db = self.db
        pm = db.project_metrics()
        cfg = db.config()
        self.name.setText(cfg.get("name", "Project"))
        self.sub.setText(f"{db.path}   \u00b7   analyzed {db.get_meta('analyzed_at', 'never')}")
        for key, lbl in self.values.items():
            v = pm.get(key, 0)
            lbl.setText(f"{v:,}" if isinstance(v, int) else fmt_value(v))
        for t in (self.complex, self.files, self.langs):
            t.clear()
        funcs, vals = db.metric_table(sorted(M.FUNCTION_KINDS))
        top = sorted((e for e in funcs if e.id in vals), key=lambda e: -vals[e.id].get("Cyclomatic", 0))[:25]
        for e in top:
            self._row(self.complex, [e.longname, fmt_value(vals[e.id].get("Cyclomatic")),
                                     fmt_value(vals[e.id].get("CountLineCode"))], e.id, e.kind)
        files, fvals = db.metric_table([M.FILE])
        big = sorted(files, key=lambda e: -fvals.get(e.id, {}).get("CountLineCode", 0))[:25]
        for e in big:
            m = fvals.get(e.id, {})
            self._row(self.files, [e.longname, fmt_value(m.get("CountLineCode")), fmt_value(m.get("CountDeclFunction"))],
                      e.id, M.FILE)
        from . import charts
        self.sunburst.set_files([(e.longname, int(fvals.get(e.id, {}).get("CountLineCode", 0)), e.id)
                                 for e in files])
        pre = pm.get("CountLinePreprocessor", 0)
        self.lines.set_items([("Code", max(0, pm["CountLineCode"] - pre), charts.LINE_COLORS["Code"]),
                              ("Comment", pm["CountLineComment"], charts.LINE_COLORS["Comment"]),
                              ("Preprocessor", pre, charts.LINE_COLORS["Preprocessor"]),
                              ("Blank", pm["CountLineBlank"], charts.LINE_COLORS["Blank"])])
        self.bubbles.set_items([(e.longname, vals[e.id].get("Cyclomatic", 0), e.id) for e in top[:12]])
        for lang, n in sorted(pm["Languages"].items()):
            loc = db.conn.execute("SELECT COALESCE(SUM(m.value),0) FROM metrics m JOIN entities e ON e.id=m.entity_id "
                                  "JOIN files f ON f.id=e.file_id WHERE e.kind='File' AND m.name='CountLineCode' "
                                  "AND f.lang=?", (lang,)).fetchone()[0]
            self._row(self.langs, [display_name(lang), n, f"{int(loc):,}"])


def _squarify(items, x, y, w, h):
    """Squarified treemap layout. items: [(value, payload)] sorted descending."""
    out = []
    items = [it for it in items if it[0] > 0]
    total = sum(v for v, _ in items)
    if not items or w <= 0 or h <= 0 or total <= 0:
        return out
    scale = w * h / total
    rest = [(v * scale, p) for v, p in items]

    def worst(row, side):
        s = sum(r for r, _ in row)
        if s == 0:
            return float("inf")
        mx, mn = max(r for r, _ in row), min(r for r, _ in row)
        return max(side * side * mx / (s * s), (s * s) / (side * side * mn))

    while rest:
        side = min(w, h)
        row = [rest.pop(0)]
        while rest and worst(row + [rest[0]], side) <= worst(row, side):
            row.append(rest.pop(0))
        s = sum(r for r, _ in row)
        if w >= h:
            cw = s / h
            cy = y
            for r, p in row:
                ch = r / cw
                out.append((QRectF(x, cy, cw, ch), p))
                cy += ch
            x += cw
            w -= cw
        else:
            rh = s / w
            cx = x
            for r, p in row:
                rw = r / rh
                out.append((QRectF(cx, y, rw, rh), p))
                cx += rw
            y += rh
            h -= rh
    return out


class TreemapCanvas(QWidget):
    entitySelected = Signal(int)
    entityActivated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.cells = []
        self.groups = []
        self.data = []
        self.color_range = (0, 1)
        self.size_metric = self.color_metric = ""
        self.setMinimumSize(300, 200)

    def set_data(self, data, size_metric, color_metric):
        """data: [(group, entity, size, color)]"""
        self.data = data
        self.size_metric, self.color_metric = size_metric, color_metric
        cs = [d[3] for d in data]
        self.color_range = (min(cs), max(cs)) if cs else (0, 1)
        self.relayout()

    def relayout(self):
        self.cells = []
        self.groups = []
        groups = {}
        for g, e, s, c in self.data:
            groups.setdefault(g, []).append((s, (e, s, c)))
        gitems = sorted(((sum(v for v, _ in lst), (g, lst)) for g, lst in groups.items()), key=lambda x: -x[0])
        for rect, (g, lst) in _squarify(gitems, 0, 0, self.width(), self.height()):
            self.groups.append((rect, g))
            inner = rect.adjusted(2, 16 if rect.height() > 40 else 2, -2, -2)
            for r, payload in _squarify(sorted(lst, key=lambda x: -x[0]), inner.x(), inner.y(), inner.width(),
                                         inner.height()):
                self.cells.append((r, payload))
        self.update()

    def resizeEvent(self, e):
        self.relayout()

    STOPS = ((0.0, (76, 154, 106)), (0.5, (214, 168, 74)), (1.0, (204, 84, 84)))

    def _color(self, v):
        lo, hi = self.color_range
        t = 0.0 if hi <= lo else (v - lo) / (hi - lo)
        t = math.sqrt(max(0.0, min(1.0, t)))
        for (t0, c0), (t1, c1) in zip(self.STOPS, self.STOPS[1:]):
            if t <= t1:
                k = (t - t0) / (t1 - t0)
                return QColor(*(round(a + (b - a) * k) for a, b in zip(c0, c1)))
        return QColor(*self.STOPS[-1][1])

    def paintEvent(self, e):
        p = QPainter(self)
        t = theme.tokens()
        p.fillRect(self.rect(), QColor(t["bg"]))
        small = QFont(self.font())
        small.setPointSize(8)
        p.setFont(small)
        for r, (ent, s, c) in self.cells:
            p.fillRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self._color(c))
            p.setPen(QPen(QColor(t["bg"]), 1))
            p.drawRect(r)
            if r.width() > 40 and r.height() > 14:
                p.setPen(QColor("#10161d"))
                p.drawText(r.adjusted(3, 1, -2, -1), Qt.AlignLeft | Qt.AlignTop | Qt.TextWrapAnywhere, ent.name)
        bold = QFont(small)
        bold.setBold(True)
        p.setFont(bold)
        for r, g in self.groups:
            p.setPen(QPen(QColor(t["border"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            if r.height() > 40:
                p.setPen(QColor(t["fg"]))
                p.drawText(r.adjusted(4, 1, -2, 0), Qt.AlignLeft | Qt.AlignTop, g)

    def _cell_at(self, pos):
        for r, payload in self.cells:
            if r.contains(pos):
                return payload
        return None

    def mouseMoveEvent(self, e):
        c = self._cell_at(e.position())
        if c:
            ent, s, col = c
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"{ent.longname}\n{self.size_metric}: {fmt_value(s)}\n{self.color_metric}: {fmt_value(col)}",
                              self)

    def mousePressEvent(self, e):
        c = self._cell_at(e.position())
        if c:
            self.entitySelected.emit(c[0].id)

    def mouseDoubleClickEvent(self, e):
        c = self._cell_at(e.position())
        if c:
            self.entityActivated.emit(c[0].id)


class TreemapView(QWidget):
    entitySelected = Signal(int)
    entityActivated = Signal(int)

    OPTIONS = {
        "Functions": (sorted(M.FUNCTION_KINDS), ["CountLineCode", "CountLine", "CountStmt", "HalsteadVolume"],
                      ["Cyclomatic", "MaxNesting", "CountParams", "CountInput", "CountOutput", "MaintainabilityIndex"]),
        "Classes": (sorted(M.CLASS_KINDS), ["CountLineCode", "CountDeclMethod", "SumCyclomatic"],
                    ["SumCyclomatic", "MaxCyclomatic", "CountClassCoupled", "PercentLackOfCohesion",
                     "MaxInheritanceTree"]),
        "Files": ([M.FILE], ["CountLineCode", "CountLine", "CountDeclFunction"],
                  ["MaxCyclomatic", "AvgCyclomatic", "SumCyclomatic", "RatioCommentToCode"]),
    }

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.title = "Metrics Treemap"
        self.what = QComboBox()
        self.what.addItems(list(self.OPTIONS))
        self.size = QComboBox()
        self.color = QComboBox()
        self.canvas = TreemapCanvas(self)
        top = QHBoxLayout()
        for lbl, w in (("Entities:", self.what), ("Size:", self.size), ("Color:", self.color)):
            top.addWidget(QLabel(lbl))
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(QLabel("<span style='color:#50aa5a'>■</span> low  "
                             "<span style='color:#ebd250'>■</span>  <span style='color:#eb5050'>■</span> high"))
        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.canvas, 1)
        self.what.currentTextChanged.connect(self._what)
        self.size.currentTextChanged.connect(self.refresh)
        self.color.currentTextChanged.connect(self.refresh)
        self.canvas.entitySelected.connect(self.entitySelected)
        self.canvas.entityActivated.connect(self.entityActivated)
        self._what(self.what.currentText())

    def _what(self, w):
        kinds, sizes, colors = self.OPTIONS[w]
        for box, items in ((self.size, sizes), (self.color, colors)):
            box.blockSignals(True)
            box.clear()
            box.addItems(items)
            box.blockSignals(False)
        self.refresh()

    def refresh(self):
        kinds, _, _ = self.OPTIONS[self.what.currentText()]
        sm, cm = self.size.currentText(), self.color.currentText()
        if not sm or not cm:
            return
        ents, vals = self.db.metric_table(kinds)
        files = {f.id: f.relpath for f in self.db.files()}
        data = []
        for e in ents:
            if e.file_id is None:
                continue
            m = vals.get(e.id, {})
            s = m.get(sm, 0) or 0
            if s <= 0:
                continue
            group = posixpath.dirname(files.get(e.file_id, "")) or "."
            if self.what.currentText() != "Files":
                group = files.get(e.file_id, "")
            data.append((group, e, s, m.get(cm, 0) or 0))
        self.canvas.set_data(data, sm, cm)


class DependencyMatrix(QWidget):
    """Dependency structure matrix: cell (row, col) = references from row to col."""
    entityActivated = Signal(int)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.title = "Dependency Matrix"
        self.level = QComboBox()
        self.level.addItems(["Directories", "Files"])
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.info = QLabel("Cell (row, column) = number of references from the row to the column. "
                           "Cells above the diagonal indicate upward dependencies; symmetric cells reveal cycles.")
        self.info.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(QLabel("Level:"))
        top.addWidget(self.level)
        top.addStretch(1)
        self.info.setObjectName("Muted")
        self.info.setContentsMargins(10, 6, 10, 6)
        self.table.setFrameShape(QTableWidget.NoFrame)
        v = _page(self)
        v.addWidget(_bar(top))
        v.addWidget(self.info)
        v.addWidget(self.table, 1)
        self.level.currentIndexChanged.connect(self.refresh)
        self.table.cellDoubleClicked.connect(self._dbl)
        self.refresh()

    def refresh(self):
        files = {f.id: f for f in self.db.files()}
        if self.level.currentIndex() == 0:
            deps = directory_dependencies(self.db)
            names = sorted({posixpath.dirname(f.relpath) or "." for f in files.values()})
            self.keys = names
            labels = [n + "/" for n in names]
        else:
            raw = file_dependencies(self.db)
            deps = {(a, b): n for a, m in raw.items() for b, n in m.items()}
            order = sorted(files, key=lambda i: files[i].relpath)
            self.keys = order
            labels = [files[i].relpath for i in order]
        index = {k: i for i, k in enumerate(self.keys)}
        n = len(self.keys)
        self.table.clear()
        self.table.setRowCount(n)
        self.table.setColumnCount(n)
        self.table.setVerticalHeaderLabels([f"{i + 1}. {l}" for i, l in enumerate(labels)])
        self.table.setHorizontalHeaderLabels([str(i + 1) for i in range(n)])
        mx = max(deps.values()) if deps else 1
        dark = theme.is_dark()
        for (a, b), cnt in deps.items():
            if a not in index or b not in index:
                continue
            i, j = index[a], index[b]
            it = QTableWidgetItem(str(cnt))
            it.setTextAlignment(Qt.AlignCenter)
            t = 0.25 + 0.75 * math.log1p(cnt) / math.log1p(mx)
            cyc = deps.get((b, a))
            base = QColor(200, 70, 60) if cyc else QColor(40, 110, 200)
            c = QColor(base)
            c.setAlphaF(min(1.0, t) * (0.8 if not dark else 0.9))
            it.setBackground(c)
            it.setToolTip(f"{labels[i]} → {labels[j]}: {cnt} references" + ("  (cycle)" if cyc else ""))
            self.table.setItem(i, j, it)
        for i in range(n):
            it = QTableWidgetItem("")
            it.setBackground(QColor(theme.tokens()["border"]))
            self.table.setItem(i, i, it)
        self.table.horizontalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _dbl(self, row, col):
        if self.level.currentIndex() == 1:
            f = self.db.file(self.keys[row])
            if f:
                self.entityActivated.emit(f.entity_id)
