"""Shared GUI helpers: icons, kind groups, worker threads."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from ..core import model as M

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
# badge colours per kind (saturated enough for light and dark backgrounds)
KIND_COLORS = {
    M.FILE: "#7d8590", M.NAMESPACE: "#a371f7", M.PACKAGE: "#a371f7",
    M.CLASS: "#d4962a", M.STRUCT: "#c9822a", M.UNION: "#c9822a", M.INTERFACE: "#c9a227",
    M.ENUM: "#b07ee8", M.ENUMERATOR: "#9a7cc7", M.TYPEDEF: "#b07ee8", M.MACRO: "#e0703d",
    M.FUNCTION: "#3fa96b", M.METHOD: "#2f9e9e", M.CONSTRUCTOR: "#2f9e9e",
    M.PROPERTY: "#4f8fd9", M.FIELD: "#4f8fd9", M.VARIABLE: "#4f8fd9",
    M.PARAMETER: "#6e7681", M.LOCAL: "#6e7681",
}
_ICON_CACHE = {}


def kind_icon(kind: str) -> QIcon:
    icon = _ICON_CACHE.get(kind)
    if icon is not None:
        return icon
    icon = QIcon()
    unresolved = kind in M.UNRESOLVED_KINDS
    color = QColor(KIND_COLORS.get(kind, "#8b949e"))
    for size in (16, 20, 32, 40):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 32.0, size / 32.0)
        r = QRectF(3, 3, 26, 26)
        if unresolved:
            pen = QPen(QColor("#8b949e"), 2.2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor("#8b949e"))
            letter = "?"
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            if kind in M.FUNCTION_KINDS or kind == M.MACRO:
                p.drawEllipse(r)
            elif kind == M.FILE:
                path = QPainterPath()
                path.moveTo(7, 2)
                path.lineTo(20, 2)
                path.lineTo(27, 9)
                path.lineTo(27, 30)
                path.lineTo(7, 30)
                path.closeSubpath()
                p.drawPath(path)
            else:
                p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor("#ffffff"))
            letter = _LETTERS.get(kind, "?")
        f = QFont()
        f.setPixelSize(18)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 1, 32, 32), Qt.AlignCenter, letter)
        p.end()
        icon.addPixmap(pm)
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
