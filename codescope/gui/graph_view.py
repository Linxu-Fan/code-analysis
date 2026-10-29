"""Interactive graph viewer built on QGraphicsView."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel,
                               QMessageBox, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core import graphs as G
from ..core import model as M
from ..core.layout import layout
from ..core.style import EDGE_COLORS, FOCUS_BORDER, node_style
from ..core.svg import to_dot, to_svg
from . import theme

FONT = QFont("Segoe UI", 9)
MONO = QFont("Consolas", 9)
MONO.setStyleHint(QFont.Monospace)


def _measure_factory():
    fm = QFontMetricsF(FONT)
    fmm = QFontMetricsF(MONO)

    def measure(label):
        lines = label.split("\n")
        mono = len(lines) > 1
        m = fmm if mono else fm
        w = max(m.horizontalAdvance(l) for l in lines) + 28
        h = len(lines) * m.height() + 16
        return max(w, 50.0), max(h, 28.0)
    return measure


class NodeItem(QGraphicsItem):
    def __init__(self, node, view):
        super().__init__()
        self.node = node
        self.view = view
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setPos(node.x, node.y)
        self.setToolTip(node.label if "\n" not in node.label else node.label.split("\n")[0])
        self.hover = False
        self.setZValue(2)

    def boundingRect(self):
        w, h = self.node.width, self.node.height
        return QRectF(-w / 2 - 3, -h / 2 - 3, w + 6, h + 6)

    def shape_path(self):
        n = self.node
        w, h = n.width, n.height
        fill, border, shape = node_style(n.kind)
        p = QPainterPath()
        r = QRectF(-w / 2, -h / 2, w, h)
        if shape == "diamond":
            p.addPolygon(QPolygonF([QPointF(0, -h / 2), QPointF(w / 2, 0), QPointF(0, h / 2), QPointF(-w / 2, 0)]))
            p.closeSubpath()
        elif shape == "ellipse":
            p.addEllipse(r)
        elif shape == "round":
            p.addRoundedRect(r, 10, 10)
        else:
            p.addRoundedRect(r, 2, 2)
        return p

    def paint(self, painter, option, widget=None):
        n = self.node
        fill, border, shape = node_style(n.kind)
        dark = theme.current["name"] == "dark"
        if n.style.get("focus"):
            border = FOCUS_BORDER
        pen = QPen(QColor(border), 2.4 if (n.style.get("focus") or self.isSelected()) else 1.2)
        if shape == "dashed":
            pen.setStyle(Qt.DashLine)
        if self.isSelected():
            pen.setColor(QColor("#1f6feb"))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(pen)
        fc = QColor(fill)
        if dark and not n.kind.startswith("cfg:start") and not n.kind.startswith("cfg:end"):
            fc = fc.darker(330)
        if self.hover:
            fc = fc.lighter(110) if not dark else fc.lighter(140)
        painter.setBrush(QBrush(fc))
        painter.drawPath(self.shape_path())
        lines = n.label.split("\n")
        uml = n.style.get("uml")
        mono = len(lines) > 1
        f = MONO if mono else FONT
        fm = QFontMetricsF(f)
        lh = fm.height()
        y = -len(lines) * lh / 2
        text_color = QColor("#ffffff") if n.kind in ("cfg:start", "cfg:end") else \
            QColor("#e6e6e6" if dark else "#1f2328")
        for i, ln in enumerate(lines):
            rect = QRectF(-n.width / 2 + 8, y + i * lh, n.width - 16, lh)
            if uml and ln.startswith("─"):
                painter.setPen(QPen(QColor(border), 1))
                painter.drawLine(QPointF(-n.width / 2, rect.center().y()), QPointF(n.width / 2, rect.center().y()))
                continue
            bold = QFont(f)
            if uml and i == 0:
                bold.setBold(True)
            painter.setFont(bold)
            painter.setPen(text_color)
            align = Qt.AlignCenter if (not uml or i == 0) else (Qt.AlignLeft | Qt.AlignVCenter)
            painter.drawText(rect, align, ln)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.view.nodeActivated.emit(self.node.data)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.view.nodeClicked.emit(self.node.data)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, edge):
        super().__init__()
        self.edge = edge
        pts = [QPointF(x, y) for x, y in edge.points]
        path = QPainterPath()
        if pts:
            path.moveTo(pts[0])
            if len(pts) == 2:
                path.lineTo(pts[1])
            else:
                for i in range(1, len(pts) - 1):
                    mid = (pts[i] + pts[i + 1]) / 2 if i < len(pts) - 2 else pts[i + 1]
                    path.quadTo(pts[i], mid)
        self.setPath(path)
        color = QColor(EDGE_COLORS.get(edge.kind, EDGE_COLORS.get(edge.label, "#7d8590")))
        pen = QPen(color, 1.3)
        if edge.kind in ("assoc", M.IMPLEMENT):
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.color = color
        self.setZValue(1)
        self.hollow = edge.kind in (M.INHERIT, M.IMPLEMENT)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        super().paint(painter, option, widget)
        pts = self.edge.points
        if len(pts) < 2:
            return
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        size = 11 if self.hollow else 9
        a = QPointF(x2, y2)
        b = QPointF(x2 - size * math.cos(ang - 0.42), y2 - size * math.sin(ang - 0.42))
        c = QPointF(x2 - size * math.cos(ang + 0.42), y2 - size * math.sin(ang + 0.42))
        painter.setPen(QPen(self.color, 1.3))
        painter.setBrush(QColor("#ffffff") if self.hollow else self.color)
        painter.drawPolygon(QPolygonF([a, b, c]))

    def boundingRect(self):
        return super().boundingRect().adjusted(-12, -12, 12, 12)


class GraphCanvas(QGraphicsView):
    nodeClicked = Signal(object)
    nodeActivated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.graph = None

    def show_graph(self, g):
        sc = self.scene()
        sc.clear()
        self.graph = g
        bg = QColor("#1e1f22" if theme.current["name"] == "dark" else "#ffffff")
        sc.setBackgroundBrush(bg)
        for e in g.edges:
            if e.points:
                sc.addItem(EdgeItem(e))
                if e.label:
                    t = QGraphicsSimpleTextItem(e.label)
                    t.setFont(QFont("Segoe UI", 8))
                    t.setBrush(QColor(EDGE_COLORS.get(e.kind, EDGE_COLORS.get(e.label, "#7d8590"))))
                    mid = e.points[len(e.points) // 2] if len(e.points) > 2 else (
                        (e.points[0][0] + e.points[-1][0]) / 2, (e.points[0][1] + e.points[-1][1]) / 2)
                    t.setPos(mid[0] + 4, mid[1] - 14)
                    t.setZValue(3)
                    sc.addItem(t)
        for n in g.nodes.values():
            sc.addItem(NodeItem(n, self))
        sc.setSceneRect(QRectF(0, 0, max(g.width, 200), max(g.height, 120)))
        self._pending_fit = True
        if self.isVisible() and self.viewport().width() > 50:
            self._initial_fit()

    def _initial_fit(self):
        self._pending_fit = False
        self.resetTransform()
        g = self.graph
        if g is not None and (g.width > self.viewport().width() or g.height > self.viewport().height()):
            self.fit()

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_pending_fit", False):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._initial_fit)

    def fit(self):
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
            if self.transform().m11() > 1.0:
                self.resetTransform()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def zoom(self, f):
        self.scale(f, f)

    def export_image(self, path):
        rect = self.scene().sceneRect()
        img = QImage(int(rect.width() * 2), int(rect.height() * 2), QImage.Format_ARGB32)
        img.fill(self.scene().backgroundBrush().color())
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self.scene().render(p, QRectF(img.rect()), rect)
        p.end()
        img.save(path)


class GraphPanel(QWidget):
    """Toolbar + canvas for one graph of one entity."""
    entityClicked = Signal(int)
    entityActivated = Signal(int)
    lineActivated = Signal(int, int)       # file id, line (control flow graphs)

    def __init__(self, db, kind, entity, parent=None):
        super().__init__(parent)
        self.db = db
        self.kind = kind
        self.entity = entity
        self.canvas = GraphCanvas(self)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        self.type_box = QComboBox()
        if entity is not None:
            for k in G.graphs_for(entity):
                self.type_box.addItem(G.GRAPH_TYPES[k], k)
        else:
            self.type_box.addItem("Architecture", "architecture")
            self.type_box.addItem("UML Class Diagram", "uml")
        idx = self.type_box.findData(kind)
        if idx < 0:
            self.type_box.addItem(G.GRAPH_TYPES.get(kind, kind), kind)
            idx = self.type_box.count() - 1
        self.type_box.setCurrentIndex(idx)
        self.depth = QSpinBox(minimum=1, maximum=10, value=3)
        self.depth.setToolTip("Depth")
        self.hide_unres = QCheckBox("Hide unresolved")
        self.lr = QCheckBox("Left to right")
        bar.addWidget(QLabel("Graph:"))
        bar.addWidget(self.type_box)
        bar.addWidget(QLabel("Depth:"))
        bar.addWidget(self.depth)
        bar.addWidget(self.hide_unres)
        bar.addWidget(self.lr)
        bar.addStretch(1)
        for text, tip, fn in (("−", "Zoom out", lambda: self.canvas.zoom(1 / 1.25)),
                              ("+", "Zoom in", lambda: self.canvas.zoom(1.25)),
                              ("Fit", "Fit to window", self.canvas.fit),
                              ("1:1", "Actual size", self.canvas.resetTransform),
                              ("Export…", "Export as PNG, SVG or DOT", self.export)):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addLayout(bar)
        v.addWidget(self.canvas)
        self.type_box.currentIndexChanged.connect(self.refresh)
        self.depth.valueChanged.connect(self.refresh)
        self.hide_unres.toggled.connect(self.refresh)
        self.lr.toggled.connect(self.refresh)
        self.canvas.nodeClicked.connect(self._clicked)
        self.canvas.nodeActivated.connect(self._activated)
        self.graph = None
        self.refresh()

    @property
    def title(self):
        name = self.entity.name if self.entity is not None else "Project"
        return f"{self.type_box.currentText()}: {name}"

    def refresh(self):
        kind = self.type_box.currentData()
        self.kind = kind
        if kind == "architecture":
            g = G.architecture_graph(self.db)
        elif kind == "uml" and self.entity is None:
            g = G.uml_graph(self.db, None)
        else:
            g = G.build_graph(self.db, kind, self.entity, self.depth.value(), self.hide_unres.isChecked())
        if self.lr.isChecked():
            g.direction = "LR" if g.direction == "TB" else "TB"
        layout(g, measure_fn=_measure_factory())
        self.graph = g
        self.canvas.show_graph(g)

    def _clicked(self, data):
        if isinstance(data, int):
            self.entityClicked.emit(data)

    def _activated(self, data):
        if isinstance(data, int):
            self.entityActivated.emit(data)
        elif isinstance(data, tuple) and data and data[0] == "line" and self.entity is not None:
            self.lineActivated.emit(self.entity.file_id, data[1])

    def export(self):
        path, flt = QFileDialog.getSaveFileName(self, "Export Graph", self.title.replace(":", "_") + ".png",
                                                "PNG image (*.png);;SVG image (*.svg);;Graphviz DOT (*.dot)")
        if not path:
            return
        try:
            if path.lower().endswith(".svg"):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(to_svg(self.graph))
            elif path.lower().endswith(".dot"):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(to_dot(self.graph))
            else:
                if not path.lower().endswith((".png", ".jpg", ".bmp")):
                    path += ".png"
                self.canvas.export_image(path)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
