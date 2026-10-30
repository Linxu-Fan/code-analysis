"""Interactive call tree: pick a function, show its ancestors and/or descendants as a tidy tree."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFileDialog, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core import calltree as CT
from ..core import model as M
from ..core.style import _mix
from . import theme

FONT = QFont("Segoe UI", 9)
MONO = QFont("Consolas", 8)
MONO.setStyleHint(QFont.Monospace)


def _colors(node):
    fill, fg, border = CT.NODE_COLORS[CT.node_category(node)]
    if theme.is_dark() and node.depth != 0:
        bg = theme.tokens()["bg"]
        return _mix(border, bg, 0.2), theme.tokens()["fg"], _mix(border, "#ffffff", 0.75)
    return fill, fg, border


class TreeNodeItem(QGraphicsItem):
    def __init__(self, node, view):
        super().__init__()
        self.node = node
        self.view = view
        self.setPos(node.x, node.y)
        self.setAcceptHoverEvents(True)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        e = node.entity
        tip = f"{e.kind}: {e.longname}"
        if node.rel == "compose" and node.via:
            tip += f"\nmember: {node.via}"
        if node.call_line:
            tip += f"\n{'declared' if view.tree is not None and view.tree.kind == 'class' else 'called'} at line {node.call_line}"
        if node.recursive:
            tip += "\n↻ recursive call (already on this path)"
        elif node.repeated:
            tip += "\n… expanded elsewhere in the tree"
        elif node.truncated and not node.children:
            tip += "\n▸ more levels beyond the depth limit"
        if getattr(node, "collapsed", None):
            tip += f"\n{len(node.collapsed)} collapsed calls (right-click to expand)"
        self.setToolTip(tip)
        self.hover = False
        self.setZValue(2)

    def boundingRect(self):
        n = self.node
        return QRectF(-n.width / 2 - 2, -n.height / 2 - 2, n.width + 4, n.height + 4)

    def paint(self, p, option, widget=None):
        n = self.node
        fill, fg, border = _colors(n)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(border), 2.2 if self.isSelected() else 1.3)
        if self.isSelected():
            pen.setColor(QColor(theme.tokens()["accent"]))
        if n.entity.is_unresolved or n.repeated:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        c = QColor(fill)
        if self.hover:
            c = c.lighter(108) if not theme.is_dark() else c.lighter(135)
        p.setBrush(c)
        r = QRectF(-n.width / 2, -n.height / 2, n.width, n.height)
        p.drawRoundedRect(r, 6, 6)
        f = QFont(FONT)
        f.setBold(n.depth == 0 or bool(n.members))
        p.setFont(f)
        p.setPen(QColor(fg))
        title = CT._display(n) + (f"  [+{len(n.collapsed)}]" if getattr(n, "collapsed", None) else "")
        if not n.members:
            p.drawText(r, Qt.AlignCenter, title)
            return
        p.drawText(QRectF(r.left(), r.top() + 3, r.width(), 20), Qt.AlignCenter, title)
        sep = r.top() + 25
        p.setPen(QPen(QColor(border), 1))
        p.drawLine(QPointF(r.left(), sep), QPointF(r.right(), sep))
        p.setFont(MONO)
        p.setPen(QColor(fg))
        for i, line in enumerate(n.members):
            p.drawText(QRectF(r.left() + 8, sep + 2 + i * CT.LINE_H, r.width() - 12, CT.LINE_H),
                       Qt.AlignLeft | Qt.AlignVCenter, line)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if e.button() == Qt.LeftButton:
            self.view.nodeClicked.emit(self.node)

    def mouseDoubleClickEvent(self, e):
        self.view.nodeActivated.emit(self.node)

    def contextMenuEvent(self, e):
        self.view.nodeMenu.emit(self.node, e.screenPos())


class TreeCanvas(QGraphicsView):
    nodeClicked = Signal(object)
    nodeActivated = Signal(object)
    nodeMenu = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.tree = None

    def show_tree(self, tree):
        sc = self.scene()
        sc.clear()
        self.tree = tree
        t = theme.tokens()
        sc.setBackgroundBrush(QColor(t["bg"]))
        edge_color = QColor(t["faint"] if theme.is_dark() else "#8b949e")
        seen_heads = set()
        for parent, child, side in CT.edges(tree):
            path = QPainterPath()
            for cmd in CT.connector(parent, child, side, getattr(tree, "level_gap", 56.0)):
                if cmd[0] == "M":
                    path.moveTo(cmd[1], cmd[2])
                elif cmd[0] == "L":
                    path.lineTo(cmd[1], cmd[2])
                else:
                    path.quadTo(cmd[1], cmd[2], cmd[3], cmd[4])
            item = QGraphicsPathItem(path)
            pen = QPen(edge_color, 1.3)
            if child.entity.is_unresolved:
                pen.setStyle(Qt.DashLine)
            item.setPen(pen)
            item.setZValue(1)
            sc.addItem(item)
            ah = QPainterPath()
            if tree.kind == "class" and child.rel == "compose":
                # UML composition: filled diamond at the containing class (path start)
                tip = path.pointAtPercent(0.0)
                ah.addPolygon(QPolygonF([tip, QPointF(tip.x() + 7, tip.y() - 4.5), QPointF(tip.x() + 14, tip.y()),
                                         QPointF(tip.x() + 7, tip.y() + 4.5), tip]))
                head = QGraphicsPathItem(ah)
                head.setBrush(edge_color)
                head.setPen(QPen(edge_color, 1.0))
            elif tree.kind == "class":
                # UML generalisation: hollow triangle pointing at the base class (path start)
                tip = path.pointAtPercent(0.0)
                ah.addPolygon(QPolygonF([tip, QPointF(tip.x() + 11, tip.y() - 6), QPointF(tip.x() + 11, tip.y() + 6),
                                         tip]))
                head = QGraphicsPathItem(ah)
                head.setBrush(QColor(t["bg"]))
                head.setPen(QPen(edge_color, 1.3))
            else:
                # arrow head at the callee end
                end = path.pointAtPercent(1.0)
                ah.addPolygon(QPolygonF([end, QPointF(end.x() - 7, end.y() - 3.5), QPointF(end.x() - 7, end.y() + 3.5)]))
                head = QGraphicsPathItem(ah)
                head.setBrush(edge_color)
                head.setPen(Qt.NoPen)
            head.setZValue(1.5)
            key = (round(ah.boundingRect().x()), round(ah.boundingRect().y()), child.rel)
            if key not in seen_heads:          # siblings share one triangle at the trunk
                seen_heads.add(key)
                sc.addItem(head)
        self.items_by_node = {}
        for n in tree.nodes():
            it = TreeNodeItem(n, self)
            sc.addItem(it)
            self.items_by_node[id(n)] = it
        sc.setSceneRect(QRectF(0, 0, tree.width, tree.height))
        self.resetTransform()
        vw, vh = self.viewport().width(), self.viewport().height()
        if tree.width > vw or tree.height > vh:
            scale = min(vw / tree.width, vh / tree.height)
            if scale >= 0.6:
                self.fit()
            else:
                self.scale(0.8, 0.8)
                self.centerOn(tree.root.x, tree.root.y)
        else:
            self.centerOn(tree.width / 2, tree.height / 2)

    def fit(self):
        self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
        if self.transform().m11() > 1:
            self.resetTransform()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
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


class CallTreePanel(QWidget):
    """Tool page: entity picker, direction ticks, depth, and the tree.

    mode "call": callers / callees of a function.  mode "class": base / derived classes.
    """
    entitySelected = Signal(int)
    entityActivated = Signal(int)
    locationActivated = Signal(int, int)

    def __init__(self, db, entity=None, parent=None, mode="call"):
        super().__init__(parent)
        self.db = db
        self.mode = mode
        self.kinds = sorted(M.FUNCTION_KINDS) if mode == "call" else sorted(M.CLASS_KINDS)
        self.noun = "Call Tree" if mode == "call" else "Class Tree"
        self.title = self.noun
        self.entity = None
        self.tree = None
        bar_w = QWidget(objectName="PanelBar")
        bar = QHBoxLayout(bar_w)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        self.picker = QLineEdit(placeholderText="Choose a function…" if mode == "call" else
                                "Choose a class or struct…", clearButtonEnabled=True)
        self.picker.setMinimumWidth(280)
        funcs = sorted({e.longname for e in db.entities(kinds=sorted(M.FUNCTION_KINDS)) if e.file_id})
        comp = QCompleter(funcs, self.picker)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setMaxVisibleItems(20)
        self.picker.setCompleter(comp)
        comp.activated.connect(self._picked)
        self.picker.returnPressed.connect(lambda: self._picked(self.picker.text()))
        call = mode == "call"
        self.down = QCheckBox("Descendants (callees)" if call else "Descendants (members && derived)")
        self.down.setChecked(True)
        self.up = QCheckBox("Ancestors (callers)" if call else "Ancestors (bases && containers)")
        self.up.setChecked(not call)
        self.depth = QSpinBox(minimum=1, maximum=20, value=4 if call else 6)
        self.depth.setFixedWidth(64)
        self.hide_unres = QCheckBox("Hide unresolved")
        self.hide_unres.setChecked(True)
        self.hide_unres.setToolTip("Hide calls into code outside the project (library functions)")
        self.expand = QCheckBox("Repeat subtrees")
        self.expand.setToolTip("Expand a function every time it appears instead of once")
        self.members = QCheckBox("Show members")
        self.members.setToolTip("List fields and methods inside each class, UML style")
        self.relation = QComboBox()
        for text, key in (("Inheritance + Composition", "both"), ("Inheritance", "inherit"),
                          ("Composition", "compose")):
            self.relation.addItem(text, key)
        self.relation.setToolTip("Inheritance: base and derived classes.\n"
                                 "Composition: the types of the data members, and the classes that "
                                 "hold this one as a member.")
        if call:
            self.members.hide()
            self.relation.hide()
        else:
            self.expand.hide()
            self.hide_unres.setText("Hide external types")
            self.hide_unres.setToolTip("Hide types defined outside the project (e.g. std::exception, int, "
                                       "Eigen::VectorXd)")
        bar.addWidget(QLabel("Function:" if call else "Class:"))
        bar.addWidget(self.picker, 1)
        bar.addWidget(self.relation)
        bar.addWidget(self.down)
        bar.addWidget(self.up)
        bar.addWidget(QLabel("Depth:"))
        bar.addWidget(self.depth)
        bar.addWidget(self.hide_unres)
        bar.addWidget(self.expand)
        bar.addWidget(self.members)
        for text, tip, fn in (("−", "Zoom out", lambda: self.canvas.scale(0.8, 0.8)),
                              ("+", "Zoom in", lambda: self.canvas.scale(1.25, 1.25)),
                              ("Fit", "Fit to window", lambda: self.canvas.fit())):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        exp = QPushButton("Export…")
        exp.setToolTip("Export the tree as SVG, PNG or indented text")
        exp.clicked.connect(self.export)
        bar.addWidget(exp)
        self.canvas = TreeCanvas(self)
        self.info = QLabel(objectName="Muted")
        self.info.setContentsMargins(10, 4, 10, 4)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(bar_w)
        v.addWidget(self.canvas, 1)
        v.addWidget(self.info)
        for w in (self.down, self.up, self.hide_unres, self.expand, self.members):
            w.toggled.connect(self.rebuild)
        self.depth.valueChanged.connect(self.rebuild)
        self.relation.currentIndexChanged.connect(self.rebuild)
        self.canvas.nodeClicked.connect(lambda n: self.entitySelected.emit(n.entity.id))
        self.canvas.nodeActivated.connect(self._activate)
        self.canvas.nodeMenu.connect(self._menu)
        if entity is not None:
            self.set_function(entity)
        else:
            self.info.setText(f"Pick a {'function' if call else 'class or struct'}, then tick Descendants, "
                              "Ancestors or both.")

    # ---- building -----------------------------------------------------------------------------
    def _picked(self, text):
        text = text.strip()
        hits = [e for e in self.db.entities(kinds=self.kinds) if e.longname == text] or \
            [e for e in self.db.entities(kinds=self.kinds, name=text)]
        if hits:
            self.set_function(hits[0])
        else:
            self.info.setText(f"No {'function' if self.mode == 'call' else 'class'} named '{text}'")

    def set_function(self, entity):
        self.entity = entity
        self.picker.blockSignals(True)
        self.picker.setText(entity.longname)
        self.picker.blockSignals(False)
        self.title = f"{self.noun}: {entity.name}"
        self.rebuild()

    def rebuild(self):
        if self.entity is None:
            return
        if not self.down.isChecked() and not self.up.isChecked():
            self.info.setText("Tick Descendants, Ancestors or both.")
            self.canvas.scene().clear()
            return
        if self.mode == "call":
            tree = CT.build_call_tree(self.db, self.entity, self.down.isChecked(), self.up.isChecked(),
                                      self.depth.value(), self.hide_unres.isChecked(), self.expand.isChecked())
        else:
            tree = CT.build_class_tree(self.db, self.entity, self.down.isChecked(), self.up.isChecked(),
                                       self.depth.value(), self.hide_unres.isChecked(), self.members.isChecked(),
                                       relation=self.relation.currentData())
        self.tree = tree
        self._layout()
        n_up = sum(1 for c in tree.up for _ in c.walk())
        n_down = sum(1 for c in tree.down for _ in c.walk())
        rel = self.relation.currentData()
        if self.mode == "call":
            up_n, down_n = "caller", "callee"
        elif rel == "inherit":
            up_n, down_n = "base class", "derived class"
        elif rel == "compose":
            up_n, down_n = "container", "member type"
        else:
            up_n, down_n = "base/container", "member/derived"
        parts = []
        if self.up.isChecked():
            parts.append(f"{n_up} {up_n} nodes")
        if self.down.isChecked():
            parts.append(f"{n_down} {down_n} nodes")
        note = "  ·  node limit reached, reduce the depth" if tree.truncated_nodes else ""
        legend = ("↻ recursion   … shown elsewhere   ▸ depth limit" if self.mode == "call"
                  else "… shown elsewhere   ▸ depth limit   △ base class   ◆ owner of a member   "
                       "name[] container  name* pointer  name? optional")
        self.info.setText(f"{self.entity.longname}: " + ", ".join(parts) + note + "   ·   " + legend +
                          "   ·   double-click opens code, right-click for more")

    def _layout(self):
        bold = QFont(FONT)
        bold.setBold(True)
        fmb = QFontMetricsF(bold)

        fmm = QFontMetricsF(MONO)

        def measure(label):
            lines = label.split("\n")
            w = fmb.horizontalAdvance(lines[0]) + 30
            if len(lines) > 1:
                w = max(w, max(fmm.horizontalAdvance(l) for l in lines[1:]) + 22)
                return max(w, 48.0), 26.0 + (len(lines) - 1) * CT.LINE_H + 8
            return max(w, 48.0), 26.0
        CT.layout_call_tree(self.tree, measure)
        self.canvas.show_tree(self.tree)

    # ---- interaction ------------------------------------------------------------------------------
    def _activate(self, node):
        if node.entity.file_id:
            self.entityActivated.emit(node.entity.id)

    def _menu(self, node, pos):
        m = QMenu(self)
        e = node.entity
        if e.file_id:
            m.addAction("Go to Definition", lambda: self.entityActivated.emit(e.id))
        if node.call_file and node.call_line and self.mode == "call":
            m.addAction(f"Go to Call Site (line {node.call_line})",
                        lambda: self.locationActivated.emit(node.call_file, node.call_line))
        if node.call_file and node.call_line and self.mode == "class":
            what = "Member Declaration" if node.rel == "compose" else "Inheritance Declaration"
            m.addAction(f"Go to {what} (line {node.call_line})",
                        lambda: self.locationActivated.emit(node.call_file, node.call_line))
        if e.kind in self.kinds and node.depth != 0:
            m.addAction(f"Show {self.noun} of This {'Function' if self.mode == 'call' else 'Class'}",
                        lambda: self.set_function(e))
        m.addSeparator()
        if node.children:
            m.addAction("Collapse Branch", lambda: self._collapse(node, True))
        if getattr(node, "collapsed", None):
            m.addAction("Expand Branch", lambda: self._collapse(node, False))
        m.addAction("View Information", lambda: self.entitySelected.emit(e.id))
        m.exec(pos)

    def _collapse(self, node, collapse):
        if collapse:
            node.collapsed = node.children
            node.children = []
        else:
            node.children = getattr(node, "collapsed", []) or []
            node.collapsed = None
        if node.depth == 0:
            return
        self._layout()

    def export(self):
        if self.tree is None:
            return
        name = (self.entity.name if self.entity else "tree").replace(":", "_").replace("~", "")
        prefix = "calltree" if self.mode == "call" else "classtree"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {self.noun}", f"{prefix}_{name}.svg",
                                              "SVG image (*.svg);;PNG image (*.png);;Text (*.txt)")
        if not path:
            return
        try:
            if path.lower().endswith(".png"):
                self.canvas.export_image(path)
            elif path.lower().endswith(".txt"):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(CT.to_text(self.tree))
            else:
                if not path.lower().endswith(".svg"):
                    path += ".svg"
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(CT.to_svg(self.tree, f"{self.noun}: {self.entity.longname}"))
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
