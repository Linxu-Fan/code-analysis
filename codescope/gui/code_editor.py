"""Source code editor with line numbers, syntax highlighting and entity navigation."""

from __future__ import annotations

import os

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QKeySequence, QPainter, QShortcut,
                           QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.languages import LANGUAGES
from . import theme


def mono_font(size=10):
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    for fam in ("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Menlo"):
        if fam in QFontDatabase.families():
            f = QFont(fam)
            break
    f.setPointSize(size)
    f.setStyleHint(QFont.Monospace)
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
        for key, color in th.items():
            if key[0].isupper():
                f = QTextCharFormat()
                f.setForeground(QColor(color))
                if key == "Comment":
                    f.setFontItalic(True)
                if key in ("Keyword", "Name.Class"):
                    f.setFontWeight(QFont.DemiBold)
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
        lexer = LANGUAGES[lang].lexer if lang in LANGUAGES else "text"
        self.highlighter = PygmentsHighlighter(self.document(), lexer)
        self._relex_timer = QTimer(self, singleShot=True, interval=400, timeout=self.highlighter.relex)
        self.textChanged.connect(self._relex_timer.start)
        self.document().modificationChanged.connect(self.modifiedChanged)
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

    def apply_theme(self):
        th = theme.code_theme()
        self.setStyleSheet(f"QPlainTextEdit {{ background:{th['background']}; color:{th['foreground']};"
                           f" selection-background-color:{th['selection']}; border:0; }}")
        if hasattr(self, "highlighter"):
            self.highlighter._build_formats()
            self.highlighter.relex()
        self._update_extra()
        self.line_area.update()

    # ---- line numbers -----------------------------------------------------------------
    def line_area_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

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
                p.setPen(QColor(th["foreground"] if num == cur else th["linenumber_fg"]))
                p.drawText(0, top, self.line_area.width() - 6, self.fontMetrics().height(),
                           Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    # ---- highlighting of current line and occurrences ---------------------------------------
    def _update_extra(self):
        th = theme.code_theme()
        sels = []
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
                s.format.setBackground(QColor("#ffe58f" if theme.current["name"] == "light" else "#5c4b16"))
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
        self.find_bar = QWidget(self)
        h = QHBoxLayout(self.find_bar)
        h.setContentsMargins(4, 2, 4, 2)
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
