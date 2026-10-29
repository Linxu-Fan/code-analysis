"""Render the application icon (PNG + multi-resolution ICO) with Qt.

Usage: python scripts/make_icon.py
"""

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter,  # noqa: E402
                           QPainterPath, QPen)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codescope", "resources")


def render(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    p.scale(s, s)
    grad = QLinearGradient(0, 0, 256, 256)
    grad.setColorAt(0, QColor("#1f6feb"))
    grad.setColorAt(1, QColor("#0b3d91"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)
    # graph: three connected nodes
    pen = QPen(QColor(255, 255, 255, 170), 10, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(70, 80), QPointF(128, 140))
    p.drawLine(QPointF(70, 80), QPointF(70, 180))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffd166"))
    p.drawEllipse(QPointF(70, 80), 24, 24)
    p.setBrush(QColor("#06d6a0"))
    p.drawEllipse(QPointF(70, 180), 20, 20)
    # magnifier
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#ffffff"), 18))
    p.drawEllipse(QPointF(150, 140), 50, 50)
    path = QPainterPath()
    p.setPen(QPen(QColor("#ffffff"), 26, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(188, 178), QPointF(222, 212))
    p.setPen(QPen(QColor("#ffffff"), 9, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(132, 140), QPointF(168, 140))
    p.drawLine(QPointF(132, 120), QPointF(156, 120))
    p.drawLine(QPointF(132, 160), QPointF(160, 160))
    del path
    p.end()
    return img


def png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main():
    QGuiApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    render(256).save(os.path.join(OUT, "codescope.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [png_bytes(render(sz)) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for sz, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(os.path.join(OUT, "codescope.ico"), "wb") as fh:
        fh.write(header + entries + b"".join(images))
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
