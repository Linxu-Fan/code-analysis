"""Dashboard charts in the style of Understand: sunburst, half donut and bubble chart."""

from __future__ import annotations

import math
import posixpath

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import theme

# Understand-like vivid categorical palette
PALETTE = ["#4e8bbf", "#5aa03c", "#93c4e6", "#acd56f", "#e8581f", "#fdae5b",
           "#9a7cc7", "#f28cb1", "#3bb3a6", "#d4b13f", "#7f9fd6", "#c96a4a"]

LINE_COLORS = {"Code": "#4e8bbf", "Comment": "#5aa03c", "Preprocessor": "#acd56f",
               "Inactive": "#93c4e6", "Blank": "#e8581f"}


def color(i):
    return QColor(PALETTE[i % len(PALETTE)])


class _Chart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 230)

    def sizeHint(self):
        return QSize(320, 270)

    def _fg(self):
        return QColor(theme.tokens()["fg"])

    def _muted(self):
        return QColor(theme.tokens()["muted"])

    def _bg(self):
        return QColor(theme.tokens()["card"])


class SunburstChart(_Chart):
    """Directory structure: rings are directory levels, outer ring files; angle = lines of code."""
    fileActivated = Signal(int)

    MAX_DEPTH = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = None
        self.segments = []          # (path, start angle, span, r0, r1, node)

    def set_files(self, files):
        """files: [(relpath, loc, entity id)]"""
        root = {"name": "", "children": {}, "value": 0, "eid": None, "path": ""}
        for rel, loc, eid in files:
            if loc <= 0:
                continue
            parts = rel.split("/")
            if len(parts) > self.MAX_DEPTH:
                parts = parts[: self.MAX_DEPTH - 1] + ["/".join(parts[self.MAX_DEPTH - 1:])]
            node = root
            node["value"] += loc
            for i, part in enumerate(parts):
                node = node["children"].setdefault(part, {"name": part, "children": {}, "value": 0, "eid": None,
                                                          "path": "/".join(parts[: i + 1])})
                node["value"] += loc
            node["eid"] = eid
        # collapse single-child chains at the top so the chart starts where the tree branches
        while len(root["children"]) == 1 and next(iter(root["children"].values()))["children"]:
            root = next(iter(root["children"].values()))
        self.root = root
        self.update()

    def _depth(self, node):
        if not node["children"]:
            return 0
        return 1 + max(self._depth(c) for c in node["children"].values())

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        self.segments = []
        if not self.root or not self.root["value"]:
            p.setPen(self._muted())
            p.drawText(self.rect(), Qt.AlignCenter, "No data")
            return
        depth = max(1, self._depth(self.root))
        side = min(self.width(), self.height()) - 16
        c = QPointF(self.width() / 2, self.height() / 2)
        hole = side * 0.17
        ring = (side / 2 - hole) / depth
        # first ring thicker, like Understand
        widths = [ring * 1.6] + [ring * (depth - 1.6) / max(depth - 1, 1)] * (depth - 1) if depth > 1 else [ring]
        radii = [hole]
        for w in widths:
            radii.append(radii[-1] + w)
        bg = self._bg()

        def draw(node, start, span, level, idx):
            if level >= depth or span == 0:
                return
            r0, r1 = radii[level], radii[level + 1]
            kids = sorted(node["children"].values(), key=lambda n: -n["value"])
            a = start
            for i, k in enumerate(kids):
                sweep = span * k["value"] / node["value"]
                col = color(idx + i if level else i)
                path = QPainterPath()
                outer = QRectF(c.x() - r1, c.y() - r1, 2 * r1, 2 * r1)
                inner = QRectF(c.x() - r0, c.y() - r0, 2 * r0, 2 * r0)
                path.arcMoveTo(outer, a)
                path.arcTo(outer, a, sweep)
                path.arcTo(inner, a + sweep, -sweep)
                path.closeSubpath()
                p.setPen(QPen(bg, 0.8 if sweep > 1.2 else 0))
                p.setBrush(col)
                p.drawPath(path)
                self.segments.append((k, a, sweep, r0, r1))
                draw(k, a, sweep, level + 1, idx + i * 3 + 1)
                a += sweep

        draw(self.root, 90, -360, 0, 0)
        p.setPen(self._fg())
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        total = self.root["value"]
        p.drawText(QRectF(c.x() - hole, c.y() - 14, 2 * hole, 18), Qt.AlignCenter, f"{total:,}")
        f.setBold(False)
        f.setPointSizeF(f.pointSizeF() * 0.85)
        p.setFont(f)
        p.setPen(self._muted())
        p.drawText(QRectF(c.x() - hole, c.y() + 2, 2 * hole, 16), Qt.AlignCenter, "lines")

    def _hit(self, pos):
        c = QPointF(self.width() / 2, self.height() / 2)
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        r = math.hypot(dx, dy)
        ang = math.degrees(math.atan2(dy, dx))
        for node, a, sweep, r0, r1 in self.segments:
            if not (r0 <= r <= r1):
                continue
            lo, hi = sorted((a, a + sweep))
            for t in (ang, ang + 360, ang - 360):
                if lo <= t <= hi:
                    return node
        return None

    def mouseMoveEvent(self, e):
        n = self._hit(e.position())
        if n is not None:
            kind = "file" if not n["children"] else "folder"
            QToolTip.showText(e.globalPosition().toPoint(), f"{n['path']}  ({kind})\n{n['value']:,} code lines", self)
            self.setCursor(Qt.PointingHandCursor if n.get("eid") else Qt.ArrowCursor)
        else:
            QToolTip.hideText()

    def mouseDoubleClickEvent(self, e):
        n = self._hit(e.position())
        if n is not None and n.get("eid"):
            self.fileActivated.emit(n["eid"])


class HalfDonutChart(_Chart):
    """Line breakdown: legend above a half donut."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []

    def set_items(self, items):
        """items: [(label, value, colour)]"""
        self.items = [(l, v, c) for l, v, c in items if v > 0]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        if not self.items:
            p.setPen(self._muted())
            p.drawText(self.rect(), Qt.AlignCenter, "No data")
            return
        fm = p.fontMetrics()
        lh = fm.height() + 6
        legend_h = lh * len(self.items)
        y = 6
        bar_w = 38
        max_text = max(fm.horizontalAdvance(f"{l}: {v:,}") for l, v, _ in self.items)
        x0 = (self.width() - (bar_w + 8 + max_text)) / 2
        for label, value, col in self.items:
            p.fillRect(QRectF(x0, y + 3, bar_w, lh - 10), QColor(col))
            p.setPen(self._fg())
            p.drawText(QPointF(x0 + bar_w + 8, y + lh - 8), f"{label}: {value:,}")
            y += lh
        avail_h = self.height() - legend_h - 16
        r = min(self.width() / 2 - 12, avail_h - 4)
        if r < 20:
            return
        c = QPointF(self.width() / 2, self.height() - 8)
        inner = r * 0.45
        total = sum(v for _, v, _ in self.items)
        a = 180.0
        for label, value, col in self.items:
            sweep = -180.0 * value / total
            path = QPainterPath()
            outer = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            inn = QRectF(c.x() - inner, c.y() - inner, 2 * inner, 2 * inner)
            path.arcMoveTo(outer, a)
            path.arcTo(outer, a, sweep)
            path.arcTo(inn, a + sweep, -sweep)
            path.closeSubpath()
            p.setPen(QPen(self._bg(), 1))
            p.setBrush(QColor(col))
            p.drawPath(path)
            a += sweep
        self._geom = (c, inner, r, total)

    def mouseMoveEvent(self, e):
        g = getattr(self, "_geom", None)
        if not g:
            return
        c, inner, r, total = g
        dx, dy = e.position().x() - c.x(), c.y() - e.position().y()
        dist = math.hypot(dx, dy)
        if dy < 0 or not (inner <= dist <= r):
            QToolTip.hideText()
            return
        ang = math.degrees(math.atan2(dy, dx))          # 0..180, 180 = left
        acc = 180.0
        for label, value, col in self.items:
            span = 180.0 * value / total
            if acc - span <= ang <= acc:
                QToolTip.showText(e.globalPosition().toPoint(), f"{label}: {value:,} ({100 * value / total:.1f}%)", self)
                return
            acc -= span


class BubbleChart(_Chart):
    """Most complex functions as packed circles; area proportional to the value."""
    entityActivated = Signal(int)
    entitySelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.circles = []

    def set_items(self, items):
        """items: [(label, value, entity id)] sorted descending"""
        self.items = [it for it in items if it[1] > 0][:12]
        self.circles = []
        self.update()

    def _pack(self, w, h):
        if not self.items:
            return []
        vmax = self.items[0][1]
        base = [math.sqrt(v / vmax) for _, v, _ in self.items]
        placed = []                    # (x, y, r) in unit space
        for i, r in enumerate(base):
            if not placed:
                placed.append((0.0, 0.0, r))
                continue
            best = None
            for step in range(1, 400):
                dist = step * 0.03
                for k in range(36):
                    ang = k * math.pi / 18 + step * 0.37
                    x, y = dist * math.cos(ang), dist * math.sin(ang) * 0.8
                    if all(math.hypot(x - px, y - py) >= pr + r + 0.06 for px, py, pr in placed):
                        best = (x, y, r)
                        break
                if best:
                    break
            placed.append(best or (0.0, 0.0, r))
        minx = min(x - r for x, y, r in placed)
        maxx = max(x + r for x, y, r in placed)
        miny = min(y - r for x, y, r in placed)
        maxy = max(y + r for x, y, r in placed)
        scale = min((w - 16) / (maxx - minx), (h - 16) / (maxy - miny))
        ox = w / 2 - (minx + maxx) / 2 * scale
        oy = h / 2 - (miny + maxy) / 2 * scale
        return [(ox + x * scale, oy + y * scale, r * scale) for x, y, r in placed]

    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        if not self.items:
            p.setPen(self._muted())
            p.drawText(self.rect(), Qt.AlignCenter, "No functions")
            return
        self.circles = self._pack(self.width(), self.height())
        values = sorted({v for _, v, _ in self.items}, reverse=True)
        f = QFont(self.font())
        f.setBold(True)
        for (label, value, eid), (x, y, r) in zip(self.items, self.circles):
            col = color([2, 5, 3, 0, 4, 1][values.index(value) % 6]) if len(values) > 1 else color(5)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x, y), r, r)
            f.setPixelSize(max(9, int(r * 0.42)))
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignCenter, f"{value:g}")

    def _hit(self, pos):
        for (label, value, eid), (x, y, r) in zip(self.items, self.circles):
            if math.hypot(pos.x() - x, pos.y() - y) <= r:
                return label, value, eid
        return None

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h:
            QToolTip.showText(e.globalPosition().toPoint(), f"{h[0]}\nCyclomatic complexity: {h[1]:g}", self)
            self.setCursor(Qt.PointingHandCursor)
        else:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h:
            self.entitySelected.emit(h[2])

    def mouseDoubleClickEvent(self, e):
        h = self._hit(e.position())
        if h:
            self.entityActivated.emit(h[2])


def dir_of(path):
    return posixpath.dirname(path)
