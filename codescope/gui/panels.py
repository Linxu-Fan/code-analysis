"""CodeCheck, Find in Files and project configuration UI."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.codecheck import DEFAULT_ENABLED, RULES, rule_settings
from ..core.languages import SELECTABLE, display_name

SEV_COLORS = {"error": "#c62828", "warning": "#b35900", "info": "#656d76"}


class CodeCheckPanel(QWidget):
    locationActivated = Signal(int, int)
    runRequested = Signal(object, object)     # enabled rule ids, thresholds

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.title = "CodeCheck"
        enabled, thr = rule_settings(db.config())
        self.rules = QTreeWidget()
        self.rules.setHeaderLabels(["Rule", "Threshold"])
        self.rules.setRootIsDecorated(True)
        cats = {}
        self.spins = {}
        for r in RULES:
            cat = cats.get(r.category)
            if cat is None:
                cat = QTreeWidgetItem(self.rules, [r.category])
                cat.setFlags(cat.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                cat.setExpanded(True)
                cats[r.category] = cat
            it = QTreeWidgetItem(cat, [f"{r.id}  {r.title}"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if r.id in enabled else Qt.Unchecked)
            it.setData(0, Qt.UserRole, r.id)
            it.setToolTip(0, (r.description or r.title) + f"\nLanguages: {', '.join(display_name(l) for l in r.langs)}")
            it.setForeground(0, QColor(SEV_COLORS[r.severity]))
            if r.threshold is not None:
                sp = QDoubleSpinBox(minimum=0, maximum=100000, decimals=0, value=thr.get(r.id, r.threshold))
                self.rules.setItemWidget(it, 1, sp)
                self.spins[r.id] = sp
        self.rules.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.rules.header().setStretchLastSection(False)
        self.rules.setColumnWidth(1, 90)
        run = QPushButton("Run CodeCheck")
        run.setProperty("primary", True)
        save = QPushButton("Save as Project Defaults")
        reset = QPushButton("Reset")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.rules)
        bh = QHBoxLayout()
        bh.addWidget(run)
        bh.addWidget(save)
        bh.addWidget(reset)
        lv.addLayout(bh)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Severity", "Rule", "Location", "Message"])
        self.results.setRootIsDecorated(False)
        self.results.setSortingEnabled(True)
        self.results.setAlternatingRowColors(True)
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sev = QComboBox()
        self.sev.addItems(["All severities", "error", "warning", "info"])
        self.search = QLineEdit(placeholderText="Filter results…", clearButtonEnabled=True)
        self.summary = QLabel("Select rules and press Run.")
        export = QPushButton("Export CSV…")
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        th = QHBoxLayout()
        th.addWidget(self.sev)
        th.addWidget(self.search, 1)
        th.addWidget(export)
        rv.addLayout(th)
        rv.addWidget(self.results, 1)
        rv.addWidget(self.summary)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([360, 800])
        split.setHandleWidth(1)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.addWidget(split)
        run.clicked.connect(self._run)
        save.clicked.connect(self._save)
        reset.clicked.connect(self._reset)
        export.clicked.connect(self._export)
        self.sev.currentIndexChanged.connect(self._filter)
        self.search.textChanged.connect(self._filter)
        self.results.itemDoubleClicked.connect(self._open)
        self.violations = []

    def selection(self):
        enabled, thr = set(), {}
        root = self.rules.invisibleRootItem()
        for i in range(root.childCount()):
            cat = root.child(i)
            for j in range(cat.childCount()):
                it = cat.child(j)
                rid = it.data(0, Qt.UserRole)
                if it.checkState(0) == Qt.Checked:
                    enabled.add(rid)
        for rid, sp in self.spins.items():
            thr[rid] = sp.value()
        return enabled, thr

    def _run(self):
        self.runRequested.emit(*self.selection())

    def _save(self):
        enabled, thr = self.selection()
        cfg = self.db.config()
        cfg["codecheck"] = {"enabled": sorted(enabled), "thresholds": thr}
        self.db.set_config(cfg)
        self.summary.setText("Rule selection saved to the project.")

    def _reset(self):
        root = self.rules.invisibleRootItem()
        for i in range(root.childCount()):
            cat = root.child(i)
            for j in range(cat.childCount()):
                it = cat.child(j)
                it.setCheckState(0, Qt.Checked if it.data(0, Qt.UserRole) in DEFAULT_ENABLED else Qt.Unchecked)
        for r in RULES:
            if r.id in self.spins:
                self.spins[r.id].setValue(r.threshold)

    def show_results(self, violations):
        self.violations = violations
        self.results.setSortingEnabled(False)
        self.results.clear()
        for v in violations:
            it = QTreeWidgetItem([v.severity, v.rule, f"{v.path}:{v.line}", v.message])
            it.setForeground(0, QColor(SEV_COLORS.get(v.severity, "#000")))
            it.setData(0, Qt.UserRole, (v.file_id, v.line))
            self.results.addTopLevelItem(it)
        self.results.setSortingEnabled(True)
        for c in range(3):
            self.results.resizeColumnToContents(c)
        counts = {s: sum(1 for v in violations if v.severity == s) for s in SEV_COLORS}
        self.summary.setText(f"{len(violations)} violations — {counts['error']} errors, "
                             f"{counts['warning']} warnings, {counts['info']} infos")
        self._filter()

    def _filter(self):
        sev = self.sev.currentText()
        q = self.search.text().lower()
        for i in range(self.results.topLevelItemCount()):
            it = self.results.topLevelItem(i)
            ok = (sev.startswith("All") or it.text(0) == sev) and (
                not q or any(q in it.text(c).lower() for c in range(4)))
            it.setHidden(not ok)

    def _open(self, item):
        fid, line = item.data(0, Qt.UserRole)
        if fid:
            self.locationActivated.emit(fid, line)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Violations", "codecheck.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["severity", "rule", "file", "line", "entity", "message"])
            for v in self.violations:
                w.writerow([v.severity, v.rule, v.path, v.line, v.entity, v.message])


class FindInFiles(QWidget):
    locationActivated = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.query = QLineEdit(placeholderText="Search text or regular expression…", clearButtonEnabled=True)
        self.regex = QCheckBox("Regex")
        self.case = QCheckBox("Match case")
        self.word = QCheckBox("Whole word")
        self.glob = QLineEdit(placeholderText="File filter, e.g. *.cpp;*.h")
        self.glob.setMaximumWidth(200)
        go = QPushButton("Search")
        go.setProperty("primary", True)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Location", "Text"])
        self.status = QLabel()
        h = QHBoxLayout()
        for w in (self.query, self.regex, self.case, self.word, self.glob, go):
            h.addWidget(w)
        h.setStretch(0, 1)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addLayout(h)
        v.addWidget(self.results, 1)
        v.addWidget(self.status)
        go.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        self.results.itemDoubleClicked.connect(self._open)

    def set_db(self, db):
        self.db = db
        self.results.clear()

    def search(self, text=None):
        if isinstance(text, str):
            self.query.setText(text)
        q = self.query.text()
        self.results.clear()
        if not q or self.db is None:
            return
        flags = 0 if self.case.isChecked() else re.IGNORECASE
        pat = q if self.regex.isChecked() else re.escape(q)
        if self.word.isChecked():
            pat = rf"\b{pat}\b"
        try:
            rx = re.compile(pat, flags)
        except re.error as exc:
            self.status.setText(f"Invalid regular expression: {exc}")
            return
        import fnmatch
        globs = [g.strip() for g in self.glob.text().split(";") if g.strip()]
        total = 0
        nfiles = 0
        for f in self.db.files():
            if globs and not any(fnmatch.fnmatch(os.path.basename(f.relpath), g) for g in globs):
                continue
            try:
                with open(f.path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().split("\n")
            except OSError:
                continue
            parent = None
            for i, line in enumerate(lines, 1):
                m = rx.search(line)
                if not m:
                    continue
                if parent is None:
                    parent = QTreeWidgetItem(self.results, [f.relpath, ""])
                    parent.setExpanded(True)
                    nfiles += 1
                it = QTreeWidgetItem(parent, [str(i), line.strip()[:300]])
                it.setData(0, Qt.UserRole, (f.id, i, m.start() + 1))
                total += 1
                if total >= 5000:
                    break
            if total >= 5000:
                break
        self.results.resizeColumnToContents(0)
        self.status.setText(f"{total} matches in {nfiles} files" + (" (limit reached)" if total >= 5000 else ""))

    def _open(self, item):
        data = item.data(0, Qt.UserRole)
        if data:
            self.locationActivated.emit(*data)


class FolderPicker(QWidget):
    """Checkable tree of a project's folders with analyzable-file counts."""
    changed = Signal()

    FILES_ROLE = Qt.UserRole + 1      # True for the "(files in this folder)" pseudo item
    PATH_ROLE = Qt.UserRole
    COUNT_ROLE = Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Folder", "Files"])
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.summary = QLabel(objectName="Muted")
        own = QPushButton("Own Code Only")
        own.setToolTip("Select everything except folders that look like third-party code or build output\n"
                       "(third_party, external, vendor, deps, build, ...)")
        all_b = QPushButton("All")
        none_b = QPushButton("None")
        self.rescan_b = QPushButton("Rescan")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        for b in (own, all_b, none_b):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.rescan_b)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Tick the folders that contain your own code, e.g. <b>src</b> and <b>include</b>. "
                      "Unticked folders are not analyzed.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        v.addWidget(hint)
        v.addLayout(bar)
        v.addWidget(self.tree, 1)
        v.addWidget(self.summary)
        self.trees = []
        self._updating = False
        own.clicked.connect(self.select_own)
        all_b.clicked.connect(lambda: self._set_all(Qt.Checked))
        none_b.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        self.tree.itemChanged.connect(self._item_changed)

    # ---- building -----------------------------------------------------------------------------
    def load(self, roots, languages, excludes, selection=None):
        """Scan *roots* and show them; *selection* None = own-code default."""
        from PySide6.QtWidgets import QApplication

        from ..core.folders import default_selection, scan_tree
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.trees = [scan_tree(r, languages, excludes) for r in roots if os.path.isdir(r)]
        finally:
            QApplication.restoreOverrideCursor()
        self._updating = True
        self.tree.clear()
        for t in self.trees:
            self._add(self.tree.invisibleRootItem(), t, top=True)
        self._updating = False
        if selection is None:
            selection = [s for t in self.trees for s in default_selection(t)]
        self.apply(selection or [{"path": t.path, "recursive": True} for t in self.trees])
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)

    def _add(self, parent, node, top=False):
        from ..gui import theme
        if node.total == 0 and not top:
            return
        it = QTreeWidgetItem(parent, [node.name, f"{node.total:,}"])
        if top:
            it.setToolTip(0, node.path)
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
        it.setData(0, self.PATH_ROLE, node.path)
        it.setData(0, self.COUNT_ROLE, node.total)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        it.setCheckState(0, Qt.Unchecked)
        it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        it.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        if node.third_party:
            it.setText(0, f"{node.name}   (third-party?)")
            it.setForeground(0, QColor(theme.tokens()["muted"]))
            it.setToolTip(0, "This looks like third-party code or build output")
        kids = [c for c in node.children if c.total]
        if node.files and kids:
            f = QTreeWidgetItem(it, ["(files in this folder)", f"{node.files:,}"])
            f.setData(0, self.PATH_ROLE, node.path)
            f.setData(0, self.FILES_ROLE, True)
            f.setData(0, self.COUNT_ROLE, node.files)
            f.setFlags(f.flags() | Qt.ItemIsUserCheckable)
            f.setCheckState(0, Qt.Unchecked)
            f.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            f.setForeground(0, QColor(theme.tokens()["muted"]))
        for c in kids:
            self._add(it, c)

    def _leaves(self, item=None):
        item = item or self.tree.invisibleRootItem()
        for i in range(item.childCount()):
            c = item.child(i)
            if c.childCount():
                yield from self._leaves(c)
            else:
                yield c

    # ---- selection ------------------------------------------------------------------------------
    def apply(self, selection):
        from ..core.folders import norm
        sel = [(norm(s["path"]), s.get("recursive", True)) for s in selection]

        def covered(path, files_only):
            for sp, rec in sel:
                if rec and (path == sp or path.startswith(sp + "/")):
                    return True
                if files_only and not rec and path == sp:
                    return True
                if not files_only and not rec and path == sp and not self._has_children(path):
                    return True
            return False
        self._updating = True
        for leaf in self._leaves():
            path = norm(leaf.data(0, self.PATH_ROLE))
            ok = covered(path, bool(leaf.data(0, self.FILES_ROLE)))
            leaf.setCheckState(0, Qt.Checked if ok else Qt.Unchecked)
        self._updating = False
        self._summary()

    def _has_children(self, path):
        return False

    def selection(self):
        """Minimal folder selection for the config; [] when everything is selected."""
        out = []

        def walk(item):
            st = item.checkState(0)
            if st == Qt.Unchecked:
                return
            if st == Qt.Checked:
                out.append({"path": item.data(0, self.PATH_ROLE),
                            "recursive": not bool(item.data(0, self.FILES_ROLE))})
                return
            for i in range(item.childCount()):
                walk(item.child(i))
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))
        if all(root.child(i).checkState(0) == Qt.Checked for i in range(root.childCount())):
            return []
        return out

    def selected_files(self):
        return sum(leaf.data(0, self.COUNT_ROLE) or 0 for leaf in self._leaves()
                   if leaf.checkState(0) == Qt.Checked)

    def total_files(self):
        return sum(t.total for t in self.trees)

    def select_own(self):
        from ..core.folders import default_selection
        self.apply([s for t in self.trees for s in default_selection(t)])
        self.changed.emit()

    def _set_all(self, state):
        self._updating = True
        for leaf in self._leaves():
            leaf.setCheckState(0, state)
        self._updating = False
        self._summary()
        self.changed.emit()

    def _item_changed(self, item, col):
        if not self._updating and col == 0:
            self._summary()
            self.changed.emit()

    def _summary(self):
        if not self.trees:
            self.summary.setText("Add a source directory to choose its folders.")
            return
        self.summary.setText(f"{self.selected_files():,} of {self.total_files():,} analyzable files selected")


class ProjectDialog(QDialog):
    """New project / project configuration dialog."""

    def __init__(self, parent=None, cfg=None, db_path=None, new=True):
        super().__init__(parent)
        self.setWindowTitle("New Project" if new else "Configure Project")
        self.resize(1060, 660)
        cfg = cfg or {}
        self.new = new
        self.name = QLineEdit(cfg.get("name", ""))
        self.db_path = QLineEdit(db_path or "")
        browse_db = QPushButton("Browse\u2026")
        self.db_path.setEnabled(new)
        browse_db.setEnabled(new)
        dbrow = QHBoxLayout()
        dbrow.addWidget(self.db_path, 1)
        dbrow.addWidget(browse_db)
        self.roots = QListWidget()
        self.roots.setFixedHeight(72)
        self.roots.setToolTip("Top-level project folders; choose which sub folders to analyze on the right")
        for r in cfg.get("roots", []):
            self.roots.addItem(r)
        add = QPushButton("Add Folder\u2026")
        add.setProperty("primary", True)
        rem = QPushButton("Remove")
        rb = QHBoxLayout()
        rb.addWidget(add)
        rb.addWidget(rem)
        rb.addStretch(1)
        rrow = QVBoxLayout()
        rrow.addWidget(self.roots)
        rrow.addLayout(rb)
        langs = QGroupBox("Languages")
        lh = QHBoxLayout(langs)
        self.lang_boxes = {}
        enabled = set(cfg.get("languages") or SELECTABLE)
        for key in SELECTABLE:
            cb = QCheckBox(display_name(key))
            cb.setChecked(key in enabled)
            lh.addWidget(cb)
            self.lang_boxes[key] = cb
        lh.addStretch(1)
        self.header = QComboBox()
        self.header.addItem("Automatic (C++ if the project has C++ sources)", "auto")
        self.header.addItem("C", "c")
        self.header.addItem("C++", "cpp")
        idx = self.header.findData(cfg.get("header_lang", "auto"))
        self.header.setCurrentIndex(max(0, idx))
        self.exclude = QPlainTextEdit("\n".join(cfg.get("exclude", [])))
        self.exclude.setPlaceholderText("One glob per line, e.g. */third_party/*")
        self.exclude.setMaximumHeight(110)
        form = QFormLayout()
        form.addRow("Project name:", self.name)
        form.addRow("Database file:", dbrow)
        form.addRow("Project folders:", rrow)
        form.addRow(langs)
        form.addRow(".h files parsed as:", self.header)
        form.addRow("Exclude patterns:", self.exclude)
        langs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        lv.addLayout(form)
        lv.addStretch(1)
        self.picker = FolderPicker()
        right = QGroupBox("Folders to analyze")
        rv = QVBoxLayout(right)
        rv.addWidget(self.picker)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([500, 540])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v = QVBoxLayout(self)
        v.addWidget(split, 1)
        self.error = QLabel()
        self.error.setStyleSheet("color:#c62828")
        v.addWidget(self.error)
        v.addWidget(buttons)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        add.clicked.connect(self._add)
        rem.clicked.connect(self._remove)
        browse_db.clicked.connect(self._browse_db)
        self.picker.rescan_b.clicked.connect(lambda: self.rescan(keep=True))
        for cb in self.lang_boxes.values():
            cb.toggled.connect(lambda _: self.rescan(keep=True))
        if self.roots.count():
            saved = cfg.get("folders") or []
            self.rescan(selection=saved if saved else [{"path": r, "recursive": True} for r in cfg.get("roots", [])])

    def _languages(self):
        return [k for k, cb in self.lang_boxes.items() if cb.isChecked()]

    def _excludes(self):
        return [l.strip() for l in self.exclude.toPlainText().splitlines() if l.strip()]

    def _root_list(self):
        return [self.roots.item(i).text() for i in range(self.roots.count())]

    def rescan(self, keep=False, selection=None):
        """Rescan the project folders; *keep* preserves the current ticks where possible."""
        from ..core.folders import default_selection, norm, scan_tree
        if keep:
            selection = self.picker.selection() if self.picker.trees else None
            if selection == [] and self.picker.trees:
                selection = [{"path": t.path, "recursive": True} for t in self.picker.trees]
            known = {t.path for t in self.picker.trees}
            new_roots = [r for r in self._root_list() if norm(r) not in known]
            if selection is not None and new_roots:
                for r in new_roots:
                    if os.path.isdir(r):
                        selection += default_selection(scan_tree(r, self._languages(), self._excludes()))
        self.picker.load(self._root_list(), self._languages(), self._excludes(), selection)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Add Project Folder")
        if d:
            d = os.path.abspath(d)
            if not self.roots.findItems(d, Qt.MatchExactly):
                self.roots.addItem(d)
            if not self.name.text():
                self.name.setText(os.path.basename(d))
            if not self.db_path.text():
                self.db_path.setText(os.path.join(d, os.path.basename(d) + ".csdb"))
            self.rescan(keep=True)

    def _remove(self):
        for i in self.roots.selectedItems():
            self.roots.takeItem(self.roots.row(i))
        self.rescan(keep=True)

    def _browse_db(self):
        p, _ = QFileDialog.getSaveFileName(self, "Project Database", self.db_path.text() or "project.csdb",
                                           "CodeScope project (*.csdb)")
        if p:
            if not p.endswith(".csdb"):
                p += ".csdb"
            self.db_path.setText(p)

    def _accept(self):
        if self.roots.count() == 0:
            self.error.setText("Add at least one project folder.")
            return
        if self.new and not self.db_path.text().strip():
            self.error.setText("Choose where to store the project database.")
            return
        if not any(cb.isChecked() for cb in self.lang_boxes.values()):
            self.error.setText("Select at least one language.")
            return
        if self.picker.trees and self.picker.selected_files() == 0:
            self.error.setText("Select at least one folder to analyze.")
            return
        self.accept()

    def result_config(self):
        return {
            "name": self.name.text().strip() or "Untitled",
            "roots": self._root_list(),
            "languages": self._languages(),
            "header_lang": self.header.currentData(),
            "exclude": self._excludes(),
            "folders": self.picker.selection(),
        }
