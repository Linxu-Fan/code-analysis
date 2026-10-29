"""Vector line icons drawn with QPainter (crisp at any DPI, recoloured per theme)."""

from __future__ import annotations

import math
import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_CACHE = {}


def _pen(p, color, w=1.6):
    pen = QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _draw(name, p, fg, accent):
    """Draw icon *name* on a 24x24 canvas."""
    _pen(p, fg)
    if name == "new":
        path = QPainterPath()
        path.moveTo(6, 3)
        path.lineTo(14, 3)
        path.lineTo(19, 8)
        path.lineTo(19, 21)
        path.lineTo(6, 21)
        path.closeSubpath()
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(14, 3), QPointF(14, 8), QPointF(19, 8)]))
        _pen(p, accent, 1.8)
        p.drawLine(QPointF(12.5, 11.5), QPointF(12.5, 17.5))
        p.drawLine(QPointF(9.5, 14.5), QPointF(15.5, 14.5))
    elif name == "open":
        path = QPainterPath()
        path.moveTo(3, 6)
        path.lineTo(9, 6)
        path.lineTo(11, 8)
        path.lineTo(20, 8)
        path.lineTo(20, 19)
        path.lineTo(3, 19)
        path.closeSubpath()
        p.drawPath(path)
        _pen(p, accent)
        p.drawLine(QPointF(3, 11), QPointF(20, 11))
    elif name == "save":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.drawRect(QRectF(8, 4, 8, 5))
        _pen(p, accent)
        p.drawRect(QRectF(7.5, 13, 9, 7))
    elif name == "analyze":
        _pen(p, accent, 1.8)
        p.drawArc(QRectF(4, 4, 16, 16), 30 * 16, 250 * 16)
        p.drawPolyline(QPolygonF([QPointF(19.5, 3.5), QPointF(19.5, 8.5), QPointF(14.5, 8.5)]))
        _pen(p, fg)
        p.drawLine(QPointF(10, 12), QPointF(14, 12))
    elif name in ("back", "forward"):
        s = -1 if name == "back" else 1
        c = 12
        p.drawLine(QPointF(c - 7 * s, 12), QPointF(c + 7 * s, 12))
        p.drawPolyline(QPolygonF([QPointF(c + 1 * s, 6), QPointF(c + 7 * s, 12), QPointF(c + 1 * s, 18)]))
    elif name == "search":
        p.drawEllipse(QRectF(4, 4, 12, 12))
        _pen(p, accent, 2.2)
        p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))
    elif name == "find_files":
        p.drawRect(QRectF(3, 3, 11, 15))
        p.drawLine(QPointF(6, 7), QPointF(11, 7))
        p.drawLine(QPointF(6, 10), QPointF(9, 10))
        _pen(p, accent, 1.8)
        p.drawEllipse(QRectF(11, 11, 7, 7))
        p.drawLine(QPointF(17, 17), QPointF(21, 21))
    elif name == "graph":
        p.drawLine(QPointF(7, 7), QPointF(17, 12))
        p.drawLine(QPointF(7, 7), QPointF(7, 17))
        p.drawLine(QPointF(7, 17), QPointF(17, 12))
        p.setBrush(QColor(accent))
        _pen(p, accent)
        p.setBrush(QColor(accent))
        for x, y in ((7, 7), (17, 12), (7, 17)):
            p.drawEllipse(QPointF(x, y), 2.8, 2.8)
    elif name == "calls":
        p.drawRoundedRect(QRectF(3, 9, 7, 6), 1.5, 1.5)
        p.drawRoundedRect(QRectF(15, 3, 6, 5), 1.5, 1.5)
        p.drawRoundedRect(QRectF(15, 16, 6, 5), 1.5, 1.5)
        _pen(p, accent)
        p.drawLine(QPointF(10, 12), QPointF(15, 5.5))
        p.drawLine(QPointF(10, 12), QPointF(15, 18.5))
    elif name == "metrics":
        _pen(p, fg)
        p.drawLine(QPointF(4, 20), QPointF(20, 20))
        _pen(p, accent, 2.6)
        for x, h in ((7, 6), (12, 12), (17, 9)):
            p.drawLine(QPointF(x, 17), QPointF(x, 17 - h))
    elif name == "treemap":
        p.drawRect(QRectF(4, 4, 16, 16))
        p.drawLine(QPointF(12, 4), QPointF(12, 20))
        p.drawLine(QPointF(12, 11), QPointF(20, 11))
        p.drawLine(QPointF(4, 14), QPointF(12, 14))
        p.fillRect(QRectF(12.8, 11.8, 6.4, 7.4), QColor(accent))
    elif name == "dsm":
        p.drawRect(QRectF(4, 4, 16, 16))
        for k in (9.33, 14.66):
            p.drawLine(QPointF(k, 4), QPointF(k, 20))
            p.drawLine(QPointF(4, k), QPointF(20, k))
        p.fillRect(QRectF(9.8, 4.6, 4.3, 4.3), QColor(accent))
        p.fillRect(QRectF(15.1, 9.9, 4.3, 4.3), QColor(accent))
    elif name == "check":
        path = QPainterPath()
        path.moveTo(12, 3)
        path.lineTo(19, 6)
        path.lineTo(19, 12)
        path.cubicTo(19, 16, 16, 19, 12, 21)
        path.cubicTo(8, 19, 5, 16, 5, 12)
        path.lineTo(5, 6)
        path.closeSubpath()
        p.drawPath(path)
        _pen(p, accent, 1.9)
        p.drawPolyline(QPolygonF([QPointF(8.5, 12), QPointF(11, 14.5), QPointF(15.5, 9.5)]))
    elif name == "report":
        p.drawRoundedRect(QRectF(5, 3, 14, 18), 1.5, 1.5)
        _pen(p, accent)
        for y in (8, 12, 16):
            p.drawLine(QPointF(8.5, y), QPointF(15.5 if y != 16 else 12.5, y))
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3, 3)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(12 + 5.5 * math.cos(a), 12 + 5.5 * math.sin(a)),
                       QPointF(12 + 8 * math.cos(a), 12 + 8 * math.sin(a)))
        p.drawEllipse(QPointF(12, 12), 5.5, 5.5)
    elif name == "architecture":
        p.drawRoundedRect(QRectF(8, 3, 8, 5), 1.2, 1.2)
        p.drawRoundedRect(QRectF(3, 16, 7, 5), 1.2, 1.2)
        p.drawRoundedRect(QRectF(14, 16, 7, 5), 1.2, 1.2)
        _pen(p, accent)
        p.drawPolyline(QPolygonF([QPointF(12, 8), QPointF(12, 12), QPointF(6.5, 12), QPointF(6.5, 16)]))
        p.drawPolyline(QPolygonF([QPointF(12, 12), QPointF(17.5, 12), QPointF(17.5, 16)]))
    elif name == "uml":
        p.drawRect(QRectF(5, 4, 14, 16))
        p.drawLine(QPointF(5, 9), QPointF(19, 9))
        p.drawLine(QPointF(5, 14), QPointF(19, 14))
        _pen(p, accent)
        p.drawLine(QPointF(8, 11.5), QPointF(14, 11.5))
        p.drawLine(QPointF(8, 17), QPointF(16, 17))
    elif name == "flow":
        p.drawPolygon(QPolygonF([QPointF(12, 3), QPointF(17, 8), QPointF(12, 13), QPointF(7, 8)]))
        _pen(p, accent)
        p.drawLine(QPointF(12, 13), QPointF(12, 17))
        p.drawRoundedRect(QRectF(8, 17, 8, 4), 1, 1)
    elif name == "info":
        p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        _pen(p, accent, 2.0)
        p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        p.drawPoint(QPointF(12, 7.8))
    elif name == "theme":
        path = QPainterPath()
        path.addEllipse(QRectF(4, 4, 16, 16))
        cut = QPainterPath()
        cut.addEllipse(QRectF(9, 1.5, 15, 15))
        p.drawPath(path.subtracted(cut))
    elif name == "close":
        p.drawLine(QPointF(7, 7), QPointF(17, 17))
        p.drawLine(QPointF(17, 7), QPointF(7, 17))
    elif name == "chevron_down":
        p.drawPolyline(QPolygonF([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)]))
    elif name == "chevron_up":
        p.drawPolyline(QPolygonF([QPointF(7, 14), QPointF(12, 9), QPointF(17, 14)]))
    elif name == "chevron_right":
        p.drawPolyline(QPolygonF([QPointF(10, 7), QPointF(15, 12), QPointF(10, 17)]))
    elif name == "tick":
        _pen(p, fg, 2.4)
        p.drawPolyline(QPolygonF([QPointF(6, 12.5), QPointF(10, 16.5), QPointF(18, 8)]))
    elif name == "project":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
        _pen(p, accent)
        p.drawLine(QPointF(7, 10), QPointF(17, 10))
        p.drawLine(QPointF(7, 14), QPointF(13, 14))
    else:
        p.drawEllipse(QRectF(5, 5, 14, 14))


def pixmap(name, fg, accent=None, size=24):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    _draw(name, p, fg, accent or fg)
    p.end()
    return pm


def icon(name, fg, accent=None, disabled=None):
    key = (name, fg, accent, disabled)
    ic = _CACHE.get(key)
    if ic is None:
        ic = QIcon()
        for sz in (16, 20, 24, 32, 48):
            ic.addPixmap(pixmap(name, fg, accent, sz), QIcon.Normal)
            if disabled:
                ic.addPixmap(pixmap(name, disabled, disabled, sz), QIcon.Disabled)
        _CACHE[key] = ic
    return ic


def export_png(name, color, size=24):
    """Write an icon to a cache file (style sheets need file paths); returns a QSS-safe path."""
    d = os.path.join(tempfile.gettempdir(), "codescope-icons")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}-{color.strip('#')}-{size}.png")
    if not os.path.exists(path):
        pixmap(name, color, color, size).save(path)
    return path.replace("\\", "/")
