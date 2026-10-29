"""CodeCheck, Find in Files and project configuration UI."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSplitter,
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
        run.setDefault(True)
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
        v = QVBoxLayout(self)
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


class ProjectDialog(QDialog):
    """New project / project configuration dialog."""

    def __init__(self, parent=None, cfg=None, db_path=None, new=True):
        super().__init__(parent)
        self.setWindowTitle("New Project" if new else "Configure Project")
        self.resize(640, 520)
        cfg = cfg or {}
        self.new = new
        self.name = QLineEdit(cfg.get("name", ""))
        self.db_path = QLineEdit(db_path or "")
        browse_db = QPushButton("Browse…")
        self.db_path.setEnabled(new)
        browse_db.setEnabled(new)
        dbrow = QHBoxLayout()
        dbrow.addWidget(self.db_path, 1)
        dbrow.addWidget(browse_db)
        self.roots = QListWidget()
        for r in cfg.get("roots", []):
            self.roots.addItem(r)
        add = QPushButton("Add Directory…")
        rem = QPushButton("Remove")
        rb = QVBoxLayout()
        rb.addWidget(add)
        rb.addWidget(rem)
        rb.addStretch(1)
        rrow = QHBoxLayout()
        rrow.addWidget(self.roots, 1)
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
        form.addRow("Source directories:", rrow)
        form.addRow(langs)
        form.addRow(".h files parsed as:", self.header)
        form.addRow("Exclude patterns:", self.exclude)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(buttons)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        add.clicked.connect(self._add)
        rem.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        browse_db.clicked.connect(self._browse_db)
        self.error = QLabel()
        self.error.setStyleSheet("color:#c62828")
        v.insertWidget(1, self.error)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Add Source Directory")
        if d:
            d = os.path.abspath(d)
            if not self.roots.findItems(d, Qt.MatchExactly):
                self.roots.addItem(d)
            if not self.name.text():
                self.name.setText(os.path.basename(d))
            if not self.db_path.text():
                self.db_path.setText(os.path.join(d, os.path.basename(d) + ".csdb"))

    def _browse_db(self):
        p, _ = QFileDialog.getSaveFileName(self, "Project Database", self.db_path.text() or "project.csdb",
                                           "CodeScope project (*.csdb)")
        if p:
            if not p.endswith(".csdb"):
                p += ".csdb"
            self.db_path.setText(p)

    def _accept(self):
        if self.roots.count() == 0:
            self.error.setText("Add at least one source directory.")
            return
        if self.new and not self.db_path.text().strip():
            self.error.setText("Choose where to store the project database.")
            return
        if not any(cb.isChecked() for cb in self.lang_boxes.values()):
            self.error.setText("Select at least one language.")
            return
        self.accept()

    def result_config(self):
        return {
            "name": self.name.text().strip() or "Untitled",
            "roots": [self.roots.item(i).text() for i in range(self.roots.count())],
            "languages": [k for k, cb in self.lang_boxes.items() if cb.isChecked()],
            "header_lang": self.header.currentData(),
            "exclude": [l.strip() for l in self.exclude.toPlainText().splitlines() if l.strip()],
        }
