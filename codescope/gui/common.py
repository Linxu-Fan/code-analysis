"""Shared GUI helpers: icons, kind groups, worker threads."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from ..core import model as M
from ..core.style import node_style

KIND_GROUPS = [
    ("All Entities", None),
    ("Files", [M.FILE]),
    ("Classes", sorted(M.CLASS_KINDS)),
    ("Functions", sorted(M.FUNCTION_KINDS)),
    ("Variables", [M.VARIABLE, M.FIELD, M.PROPERTY]),
    ("Macros", [M.MACRO]),
    ("Types", [M.TYPEDEF, M.ENUM]),
    ("Enumerators", [M.ENUMERATOR]),
    ("Namespaces", [M.NAMESPACE, M.PACKAGE]),
    ("Parameters && Locals", [M.PARAMETER, M.LOCAL]),
    ("Unresolved", sorted(M.UNRESOLVED_KINDS)),
]

_LETTERS = {
    M.FILE: "F", M.NAMESPACE: "N", M.PACKAGE: "P", M.CLASS: "C", M.STRUCT: "S", M.UNION: "U",
    M.INTERFACE: "I", M.ENUM: "E", M.ENUMERATOR: "e", M.TYPEDEF: "T", M.MACRO: "M", M.FUNCTION: "f",
    M.METHOD: "m", M.CONSTRUCTOR: "c", M.PROPERTY: "p", M.FIELD: "v", M.VARIABLE: "v", M.PARAMETER: "a",
    M.LOCAL: "l",
}
_ICON_CACHE = {}


def kind_icon(kind: str) -> QIcon:
    icon = _ICON_CACHE.get(kind)
    if icon is not None:
        return icon
    fill, border, shape = node_style(kind)
    if kind in (M.PARAMETER, M.LOCAL, M.ENUMERATOR, M.PROPERTY):
        fill, border = "#eef1f4", "#6b7280"
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(border), 2))
    p.setBrush(QColor(fill))
    r = QRectF(3, 3, 26, 26)
    if kind in M.FUNCTION_KINDS or kind == M.MACRO:
        p.drawEllipse(r)
    elif shape == "dashed":
        pen = QPen(QColor(border), 2, Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(r, 5, 5)
    elif kind == M.FILE:
        path = QPainterPath()
        path.moveTo(6, 3)
        path.lineTo(20, 3)
        path.lineTo(27, 10)
        path.lineTo(27, 29)
        path.lineTo(6, 29)
        path.closeSubpath()
        p.drawPath(path)
    else:
        p.drawRoundedRect(r, 5, 5)
    p.setPen(QColor(border))
    f = QFont()
    f.setPixelSize(16)
    f.setBold(True)
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, _LETTERS.get(kind, "?"))
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[kind] = icon
    return icon


def app_icon() -> QIcon:
    import os
    here = os.path.dirname(os.path.dirname(__file__))
    for name in ("codescope.ico", "codescope.png"):
        p = os.path.join(here, "resources", name)
        if os.path.exists(p):
            return QIcon(p)
    return kind_icon(M.CLASS)


def fmt_value(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v)


class Worker(QThread):
    """Runs *fn(progress, cancelled)* in a background thread."""
    progress = Signal(str, int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def cancelled(self):
        return self._cancel

    def run(self):
        try:
            res = self.fn(lambda s, i, n, m="": self.progress.emit(s, int(i), int(n), str(m)), self.cancelled)
            self.done.emit(res)
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.failed.emit(f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")


class Signals(QObject):
    """Application wide navigation signals."""
    entitySelected = Signal(int)            # show in information browser
    entityActivated = Signal(int)           # open definition
    locationActivated = Signal(int, int)    # file id, line
    graphRequested = Signal(str, int)       # graph kind, entity id
