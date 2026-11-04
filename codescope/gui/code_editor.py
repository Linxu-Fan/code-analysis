"""Source code editor with line numbers, syntax highlighting and entity navigation."""

from __future__ import annotations

import os

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontInfo, QKeySequence, QPainter, QPen, QShortcut,
                           QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.languages import LANGUAGES
from . import theme


EDITOR_FONTS = ("Cascadia Mono", "Consolas", "Cascadia Code", "JetBrains Mono", "Menlo", "DejaVu Sans Mono",
                "Courier New")
# Visual Studio's "Text Editor" entry in Tools > Options > Fonts and Colors
_VS_TEXT_EDITOR = "{A27B4E24-A735-4D1D-B8E7-9716E1E3D8E0}"


def visual_studio_font():
    """(family, size) of the Visual Studio text editor font, read from the settings file of the most
    recently used Visual Studio version; None if Visual Studio is not installed or uses its default."""
    import glob
    import re
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "VisualStudio")
    paths = glob.glob(os.path.join(base, "*", "Settings", "CurrentSettings*.vssettings"))
    for path in sorted(paths, key=os.path.getmtime, reverse=True):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8", "replace")
        m = re.search(r'<Category\s[^>]*GUID="' + re.escape(_VS_TEXT_EDITOR) + r'"[^>]*>', text, re.I)
        if not m:
            continue
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
        if attrs.get("FontName"):
            try:
                size = float(attrs.get("FontSize") or 10)
            except ValueError:
                size = 10.0
            return attrs["FontName"], size
    return None


def _installed(family):
    """True if *family* really resolves to that font (not to a substitute such as NSimSun)."""
    if not family:
        return False
    f = QFont(family)
    return QFontInfo(f).family().lower() == family.lower() or \
        family.lower() in (x.lower() for x in QFontDatabase.families())


def mono_font(size=None):
    """Editor font: the user's choice, else the font Visual Studio uses (its Fonts and Colors
    setting, by default Cascadia Mono / Consolas, 10 pt)."""
    family, psize = theme.current.get("editor_font") or (None, None)
    vs = visual_studio_font() if os.name == "nt" else None
    candidates = ((family,) if family else ()) + ((vs[0],) if vs else ()) + EDITOR_FONTS
    chosen = next((fam for fam in candidates if _installed(fam)), None)
    if chosen:
        f = QFont(chosen)
    elif os.name == "nt":
        f = QFont("Consolas")         # never the system fixed font (NSimSun on Chinese Windows)
    else:
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    default_size = vs[1] if vs and chosen == vs[0] else 10
    f.setPointSizeF(float(size or psize or default_size))
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    f.setStyleStrategy(QFont.PreferAntialias)
    f.setHintingPreference(QFont.PreferFullHinting)
    if hasattr(f, "setFeature"):          # Qt >= 6.7: no programming ligatures, like Visual Studio
        for tag in ("liga", "calt", "dlig"):
            try:
                f.setFeature(QFont.Tag(tag) if hasattr(QFont, "Tag") else tag, 0)
            except (TypeError, ValueError):
                pass
    return f


class PygmentsHighlighter(QSyntaxHighlighter):
    """Lexes the whole document with Pygments and applies per-block spans."""

    def __init__(self, doc, lexer_name):
        super().__init__(doc)
        from pygments.lexers import get_lexer_by_name
        try:
            self.lexer = get_lexer_by_name(lexer_name, stripnl=False, ensurenl=False)
        except Exception:  # noqa: BLE001
            from pygments.lexers import TextLexer
            self.lexer = TextLexer()
        self.spans = {}
        self.formats = {}
        self._build_formats()
        self.relex()

    def _build_formats(self):
        th = theme.code_theme()
        self.formats.clear()
        mapping = {"Keyword": th["keyword"], "Keyword.Type": th["keyword"], "Name.Function": th["function"],
                   "Name.Class": th["type"], "String": th["string"], "Number": th["number"],
                   "Comment": th["comment"], "Comment.Preproc": th["preproc"], "Name.Decorator": th["macro"]}
        for key, color in mapping.items():
            if True:
                f = QTextCharFormat()
                f.setForeground(QColor(color))
                self.formats[key] = f

    def _fmt(self, ttype):
        t = ttype
        while t is not None:
            name = str(t).replace("Token.", "")
            if name in self.formats:
                return self.formats[name]
            if name.startswith("Literal.String"):
                return self.formats["String"]
            if name.startswith("Literal.Number"):
                return self.formats["Number"]
            t = t.parent
        return None

    def relex(self):
        text = self.document().toPlainText()
        spans = {}
        line, col = 0, 0
        for ttype, value in self.lexer.get_tokens(text):
            fmt = self._fmt(ttype)
            parts = value.split("\n")
            for i, part in enumerate(parts):
                if i > 0:
                    line += 1
                    col = 0
                if part and fmt is not None:
                    spans.setdefault(line, []).append((col, len(part), fmt))
                col += len(part)
        self.spans = spans
        self.rehighlight()

    def highlightBlock(self, text):
        for start, length, fmt in self.spans.get(self.currentBlock().blockNumber(), ()):
            self.setFormat(start, length, fmt)


class _LineArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_area(event)


class CodeEditor(QPlainTextEdit):
    entityClicked = Signal(int, int, int)       # file id, line, column (single click)
    entityJump = Signal(int, int, int)          # ctrl+click / F12
    contextRequested = Signal(int, int, int, object)   # file id, line, col, QPoint (global)
    modifiedChanged = Signal(bool)

    def __init__(self, path, file_id, lang, parent=None):
        super().__init__(parent)
        self.path = path
        self.file_id = file_id
        self.lang = lang
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.line_area = _LineArea(self)
        self.blockCountChanged.connect(self._update_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._update_extra)
        self._occurrences = []
        self._mark_line = None
        self.load()
        if lang in LANGUAGES:
            from .highlight import TreeSitterHighlighter
            self.highlighter = TreeSitterHighlighter(self.document(), lang)
        else:
            self.highlighter = PygmentsHighlighter(self.document(), "text")
        self._relex_timer = QTimer(self, singleShot=True, interval=300, timeout=self.highlighter.relex)
        self.textChanged.connect(self._relex_timer.start)
        self.document().modificationChanged.connect(self.modifiedChanged)
        self.document().modificationChanged.connect(self._modified)
        self._update_width()
        self.apply_theme()
        self.setMouseTracking(True)

    # ---- file handling ------------------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding="utf-8", errors="replace", newline="") as fh:
                text = fh.read()
        except OSError as exc:
            text = f"<unable to open {self.path}: {exc}>"
        self.setPlainText(text.replace("\r\n", "\n"))
        self.document().setModified(False)

    def save(self):
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.toPlainText())
        self.document().setModified(False)

    def indent_unit(self):
        """Indentation step used by this file (2, 3, 4 or 8 columns; tabs count as 4)."""
        unit = getattr(self, "_indent_unit", None)
        if unit is None:
            counts = {}
            for line in self.toPlainText().split("\n")[:4000]:
                stripped = line.lstrip(" ")
                n = len(line) - len(stripped)
                if n and stripped and not stripped.startswith(("*", "\t")):
                    counts[n] = counts.get(n, 0) + 1
            unit = 4
            if counts and not self.toPlainText().startswith("\t"):
                for cand in (2, 3, 4, 8):
                    total = sum(counts.values())
                    ok = sum(c for n, c in counts.items() if n % cand == 0)
                    if ok >= 0.9 * total:
                        unit = cand
            self._indent_unit = unit
        return unit

    def _modified(self, mod):
        # semantic colours come from the last analysis; positions go stale once the text changes
        if mod and hasattr(self.highlighter, "disable_semantic"):
            self.highlighter.disable_semantic()

    def set_semantic(self, tokens, type_names=None, macro_names=None):
        if hasattr(self.highlighter, "set_semantic"):
            if type_names is not None:
                self.highlighter.type_names = type_names
            if macro_names is not None:
                self.highlighter.macro_names = macro_names
            self.highlighter.set_semantic(tokens)

    # ---- font / zoom ------------------------------------------------------------------
    def set_editor_font(self, font):
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._update_width()
        self.line_area.update()
        self.viewport().update()

    def zoom(self, delta):
        f = QFont(self.font())
        f.setPointSizeF(min(40.0, max(6.0, f.pointSizeF() + delta)))
        self.set_editor_font(f)

    def zoom_reset(self):
        self.set_editor_font(mono_font())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)

    # ---- Visual Studio style decorations: indent guides and current line frame ------------
    def paintEvent(self, e):
        super().paintEvent(e)
        th = theme.code_theme()
        p = QPainter(self.viewport())
        try:
            cw = self.fontMetrics().horizontalAdvance(" ")
            left = self.contentOffset().x() + self.document().documentMargin()
            pen = QPen(QColor(th["indent_guide"]), 1, Qt.DotLine)
            p.setPen(pen)
            unit = self.indent_unit()
            block = self.firstVisibleBlock()
            prev_indent = 0
            rect = e.rect()
            while block.isValid():
                geo = self.blockBoundingGeometry(block).translated(self.contentOffset())
                if geo.top() > rect.bottom():
                    break
                text = block.text()
                stripped = text.lstrip(" \t")
                if stripped:
                    col = 0
                    for ch in text[: len(text) - len(stripped)]:
                        col = (col // 4 + 1) * 4 if ch == "\t" else col + 1
                    indent = col
                else:
                    nxt = block.next()
                    nxt_indent = 0
                    while nxt.isValid() and not nxt.text().strip():
                        nxt = nxt.next()
                    if nxt.isValid():
                        t2 = nxt.text()
                        nxt_indent = len(t2) - len(t2.lstrip(" \t"))
                    indent = min(prev_indent, nxt_indent)
                if geo.bottom() >= rect.top():
                    for level in range(0, indent, unit):
                        x = int(left + level * cw + cw / 2)
                        p.drawLine(x, int(geo.top()), x, int(geo.bottom()))
                prev_indent = indent if stripped else prev_indent
                block = block.next()
            if th.get("current_line_border") and not self.textCursor().hasSelection():
                blk = self.textCursor().block()
                geo = self.blockBoundingGeometry(blk).translated(self.contentOffset())
                p.setPen(QPen(QColor(th["current_line_border"]), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRect(1, int(geo.top()) + 1, self.viewport().width() - 3, int(geo.height()) - 2))
        finally:
            p.end()

    def apply_theme(self):
        th = theme.code_theme()
        self.setStyleSheet(f"QPlainTextEdit {{ background:{th['background']}; color:{th['foreground']};"
                           f" selection-background-color:{th['selection']}; selection-color:{th['foreground']};"
                           f" border:0; border-radius:0; padding:0; }}")
        if hasattr(self, "highlighter"):
            if hasattr(self.highlighter, "refresh_theme"):
                self.highlighter.refresh_theme()
        self._update_extra()
        self.line_area.update()

    # ---- line numbers -----------------------------------------------------------------
    def line_area_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 28 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_width(self, *_):
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _update_area(self, rect, dy):
        if dy:
            self.line_area.scroll(0, dy)
        else:
            self.line_area.update(0, rect.y(), self.line_area.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.line_area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_line_area(self, event):
        th = theme.code_theme()
        p = QPainter(self.line_area)
        p.fillRect(event.rect(), QColor(th["linenumber_bg"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        cur = self.textCursor().blockNumber()
        p.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(th["linenumber_current"] if num == cur else th["linenumber_fg"]))
                p.drawText(0, top, self.line_area.width() - 14, self.fontMetrics().height(),
                           Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    # ---- highlighting of current line and occurrences ---------------------------------------
    def _update_extra(self):
        th = theme.code_theme()
        sels = []
        if th.get("current_line"):
            cl = QTextEdit.ExtraSelection()
            cl.format.setBackground(QColor(th["current_line"]))
            cl.format.setProperty(QTextFormat.FullWidthSelection, True)
            cl.cursor = self.textCursor()
            cl.cursor.clearSelection()
            sels.append(cl)
        if self._mark_line is not None:
            blk = self.document().findBlockByNumber(self._mark_line - 1)
            if blk.isValid():
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(QColor(th["mark"]))
                s.format.setProperty(QTextFormat.FullWidthSelection, True)
                s.cursor = QTextCursor(blk)
                sels.append(s)
        for line, col, length in self._occurrences:
            blk = self.document().findBlockByNumber(line - 1)
            if not blk.isValid():
                continue
            c = QTextCursor(blk)
            c.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, max(0, col - 1))
            c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, length)
            s = QTextEdit.ExtraSelection()
            s.format.setBackground(QColor(th["occurrence"]))
            s.cursor = c
            sels.append(s)
        self.setExtraSelections(sels)
        self.line_area.update()
        self.viewport().update()

    def set_occurrences(self, occ):
        self._occurrences = list(occ)
        self._update_extra()

    def goto_line(self, line, col=1, mark=True):
        blk = self.document().findBlockByNumber(max(0, line - 1))
        c = QTextCursor(blk)
        c.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, max(0, col - 1))
        self.setTextCursor(c)
        self._mark_line = line if mark else None
        self.centerCursor()
        self._update_extra()
        self.setFocus()

    def cursor_location(self, cursor=None):
        c = cursor or self.textCursor()
        return c.blockNumber() + 1, c.positionInBlock() + 1

    # ---- mouse / keyboard ----------------------------------------------------------------------
    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if e.button() == Qt.LeftButton:
            line, col = self.cursor_location(self.cursorForPosition(e.position().toPoint()))
            if e.modifiers() & Qt.ControlModifier:
                self.entityJump.emit(self.file_id, line, col)
            else:
                self.entityClicked.emit(self.file_id, line, col)
        self._mark_line = None

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        self.viewport().setCursor(Qt.PointingHandCursor if e.modifiers() & Qt.ControlModifier else Qt.IBeamCursor)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F12:
            line, col = self.cursor_location()
            self.entityJump.emit(self.file_id, line, col)
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        c = self.cursorForPosition(e.pos())
        if not self.textCursor().hasSelection():
            self.setTextCursor(c)
        line, col = self.cursor_location(c)
        self.contextRequested.emit(self.file_id, line, col, e.globalPos())

    def standard_menu(self):
        return self.createStandardContextMenu()

    # ---- search -------------------------------------------------------------------------------------
    def find_text(self, text, backward=False, case=False, whole=False):
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindBackward
        if case:
            flags |= QTextDocument.FindCaseSensitively
        if whole:
            flags |= QTextDocument.FindWholeWords
        if not self.find(text, flags):
            c = self.textCursor()
            c.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.setTextCursor(c)
            return self.find(text, flags)
        return True


class EditorPane(QWidget):
    """Editor plus an inline find bar."""

    def __init__(self, path, file_id, lang, parent=None):
        super().__init__(parent)
        self.editor = CodeEditor(path, file_id, lang, self)
        self.find_bar = QWidget(self, objectName="PanelBar")
        h = QHBoxLayout(self.find_bar)
        h.setContentsMargins(8, 4, 8, 4)
        self.find_edit = QLineEdit(placeholderText="Find in file…")
        self.case = QCheckBox("Aa")
        self.case.setToolTip("Match case")
        self.whole = QCheckBox("Word")
        prev_b = QToolButton(text="▲")
        next_b = QToolButton(text="▼")
        close_b = QToolButton(text="✕")
        for w in (self.find_edit, self.case, self.whole, prev_b, next_b, close_b):
            h.addWidget(w)
        self.find_bar.hide()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.editor)
        v.addWidget(self.find_bar)
        self.find_edit.returnPressed.connect(lambda: self._find(False))
        next_b.clicked.connect(lambda: self._find(False))
        prev_b.clicked.connect(lambda: self._find(True))
        close_b.clicked.connect(self.find_bar.hide)
        QShortcut(QKeySequence.Find, self, activated=self.show_find)
        QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar, activated=self.find_bar.hide)

    def show_find(self):
        sel = self.editor.textCursor().selectedText()
        if sel:
            self.find_edit.setText(sel)
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def _find(self, backward):
        self.editor.find_text(self.find_edit.text(), backward, self.case.isChecked(), self.whole.isChecked())

    @property
    def title(self):
        return os.path.basename(self.editor.path)
