"""A tool bar layout that wraps onto more lines instead of forcing a wide window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QWidgetItem


_GROWING = (QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps when the width runs out.

    A label ending in ':' stays on the same line as the control after it.  Leftover space on a
    line goes to items that expand horizontally (e.g. line edits).  Stretches are ignored.
    """

    def __init__(self, parent=None, spacing=8, line_spacing=6):
        super().__init__(parent)
        self._items = []
        self._hspace = spacing
        self._vspace = line_spacing

    # ---- QLayout interface ------------------------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def addStretch(self, _stretch=0):       # API compatibility with QHBoxLayout
        pass

    def addWidget(self, w, _stretch=0):
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def setSpacing(self, s):
        self._hspace = s

    def spacing(self):
        return self._hspace

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):
        m = self.contentsMargins()
        w = sum(u[1] for u in self._units()) + self._hspace * max(0, len(self._units()) - 1)
        h = max((it.sizeHint().height() for it in self._visible()), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self):
        m = self.contentsMargins()
        w = max((u[2] for u in self._units()), default=0)
        h = max((it.minimumSize().height() for it in self._visible()), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    # ---- layout -------------------------------------------------------------------------------
    def _visible(self):
        return [it for it in self._items if it.widget() is not None and not it.widget().isHidden()]

    def _units(self):
        """Groups placed together: [(items, preferred width, minimum width)]."""
        items, units, i = self._visible(), [], 0
        while i < len(items):
            group = [items[i]]
            w = items[i].widget()
            if isinstance(w, QLabel) and w.text().rstrip().endswith(":") and i + 1 < len(items):
                group.append(items[i + 1])
            i += len(group)
            gap = self._hspace * (len(group) - 1)
            pref = sum(it.sizeHint().width() for it in group) + gap
            mini = sum(max(it.minimumSize().width(), 0) for it in group) + gap
            units.append((group, pref, mini))
        return units

    def _do_layout(self, rect, apply):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines, cur, used = [], [], 0
        for unit in self._units():
            need = unit[1] if not cur else used + self._hspace + unit[1]
            if cur and need > area.width():
                lines.append(cur)
                cur, used = [], 0
                need = unit[1]
            cur.append(unit)
            used = need
        if cur:
            lines.append(cur)
        y = area.y()
        for line in lines:
            items = [it for unit in line for it in unit[0]]
            h = max(it.sizeHint().height() for it in items)
            if apply:
                widths = [min(it.sizeHint().width(), area.width()) for it in items]
                spare = area.width() - sum(widths) - self._hspace * (len(items) - 1)
                grow = [k for k, it in enumerate(items)
                        if it.widget().sizePolicy().horizontalPolicy() in _GROWING]
                if spare > 0 and grow:
                    for k in grow:
                        widths[k] += spare // len(grow)
                x = area.x()
                for it, w in zip(items, widths):
                    ih = it.sizeHint().height()
                    it.setGeometry(QRect(QPoint(x, y + (h - ih) // 2), QSize(w, ih)))
                    x += w + self._hspace
            y += h + self._vspace
        return y - self._vspace - area.y() + m.top() + m.bottom() if lines else m.top() + m.bottom()
