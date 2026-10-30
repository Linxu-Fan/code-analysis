"""Interactive call tree: pick a function, show its ancestors and/or descendants as a tidy tree."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFileDialog, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsScene, QGraphicsView, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..core import calltree as CT
from ..core import model as M
from ..core.style import _mix
from . import theme
from .flow import FlowLayout

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
        if node.rel == "write":
            tip = f"Step #{node.step}\n" + "\n".join(
                f"  {'replaces ' + name + ' (the whole object)' if whole else 'sets ' + name}  (line {line})"
                for name, line, whole in node.writes) + "\nDouble-click opens the write"
        elif node.call_line:
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
            entry = tree.kind == "modify" and parent.depth == 0      # root -> entry point: not a call
            if child.entity.is_unresolved or entry:
                pen.setStyle(Qt.DashLine)
            item.setPen(pen)
            item.setZValue(1)
            sc.addItem(item)
            if entry:
                continue
            if tree.kind == "class" and child.rel == "member":
                continue                               # member lists: plain connectors
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

    mode "call": callers / callees of a function.  mode "class": base / derived classes and
    members.  mode "modify": the call paths to every write of a class's members, in order.
    """
    entitySelected = Signal(int)
    entityActivated = Signal(int)
    locationActivated = Signal(int, int)

    def __init__(self, db, entity=None, parent=None, mode="call", member=None):
        super().__init__(parent)
        self.db = db
        self.mode = mode
        self.kinds = sorted(M.FUNCTION_KINDS) if mode == "call" else sorted(M.CLASS_KINDS)
        self.noun = {"call": "Call Tree", "class": "Class Tree", "modify": "Modification Tree"}[mode]
        self.title = self.noun
        self.entity = None
        self.tree = None
        bar_w = QWidget(objectName="PanelBar")
        bar = FlowLayout(bar_w)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        self.picker = QLineEdit(placeholderText="Choose a function…" if mode == "call" else
                                "Choose a class or struct…", clearButtonEnabled=True)
        self.picker.setMinimumWidth(220)
        funcs = sorted({e.longname for e in db.entities(kinds=self.kinds) if e.file_id})
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
        self.depth = QSpinBox(minimum=1, maximum=30, value={"call": 4, "class": 6, "modify": 10}[mode])
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
                          ("Composition", "compose"), ("Members (data + functions)", "members")):
            self.relation.addItem(text, key)
        self.relation.setToolTip("Inheritance: base and derived classes.\n"
                                 "Composition: the types of the data members, and the classes that "
                                 "hold this one as a member.\n"
                                 "Members: every data member and member function; data members of project "
                                 "types expand into their own members. Only direct ancestors are shown.")
        self.member = QComboBox()
        self.member.setMinimumWidth(150)
        self.member.setToolTip("Only the writes of this member (inherited members included)")
        self.paths = QCheckBox("Show call paths")
        self.paths.setChecked(True)
        self.paths.setToolTip("Start at the entry points (e.g. main) and follow the calls that lead to a "
                              "modification.\nUntick to list only the functions that write the members.")
        self.whole = QCheckBox("Whole-object writes")
        self.whole.setChecked(True)
        self.whole.setToolTip("Also count assignments that replace the whole object, e.g. world.options = other "
                              "when\nthe class is held by value in a field (nested holders included)")
        if mode == "modify":
            for w in (self.down, self.up, self.hide_unres, self.expand, self.members, self.relation):
                w.hide()
        else:
            self.member.hide()
            self.paths.hide()
            self.whole.hide()
        if call:
            self.members.hide()
            self.relation.hide()
        elif mode == "class":
            self.expand.hide()
            self.hide_unres.setText("Hide external types")
            self.hide_unres.setToolTip("Hide types defined outside the project (e.g. std::exception, int, "
                                       "Eigen::VectorXd)")
        bar.addWidget(QLabel("Function:" if call else "Class:"))
        bar.addWidget(self.picker, 1)
        bar.addWidget(self.relation)
        if mode == "modify":
            bar.addWidget(QLabel("Member:"))
        bar.addWidget(self.member)
        bar.addWidget(self.paths)
        bar.addWidget(self.whole)
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
        self.info = QLabel(objectName="Muted", wordWrap=True)
        self.info.setContentsMargins(10, 4, 10, 4)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(bar_w)
        v.addWidget(self.canvas, 1)
        v.addWidget(self.info)
        for w in (self.down, self.up, self.hide_unres, self.expand, self.members, self.paths, self.whole):
            w.toggled.connect(self.rebuild)
        self.member.currentIndexChanged.connect(self.rebuild)
        self.depth.valueChanged.connect(self.rebuild)
        self.relation.currentIndexChanged.connect(self._relation_changed)
        self.canvas.nodeClicked.connect(lambda n: self.entitySelected.emit(n.entity.id))
        self.canvas.nodeActivated.connect(self._activate)
        self.canvas.nodeMenu.connect(self._menu)
        if entity is not None:
            self.set_function(entity, member)
        elif mode == "modify":
            self.info.setText("Pick a class or struct, and optionally one of its members.")
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

    def set_function(self, entity, member=None):
        self.entity = entity
        self.picker.blockSignals(True)
        self.picker.setText(entity.longname)
        self.picker.blockSignals(False)
        self.title = f"{self.noun}: {entity.name}" + (f".{member.name}" if member is not None else "")
        if self.mode == "modify":
            self.member.blockSignals(True)
            self.member.clear()
            self.member.addItem("(all members)", None)
            for f in CT.modifiable_fields(self.db, entity):
                owner = self.db.parent(f)
                inherited = f"  ({owner.name})" if owner is not None and owner.id != entity.id else ""
                self.member.addItem(f"{f.name}{inherited}", f.id)
            if member is not None:
                i = self.member.findData(member.id)
                if i < 0:
                    self.member.addItem(member.name, member.id)
                    i = self.member.count() - 1
                self.member.setCurrentIndex(i)
            self.member.blockSignals(False)
        self.rebuild()

    def rebuild(self):
        if self.entity is None:
            return
        if self.mode == "modify":
            self._rebuild_modify()
            return
        if not self.down.isChecked() and not self.up.isChecked():
            self.info.setText("Tick Descendants, Ancestors or both.")
            self.canvas.scene().clear()
            return
        if self.mode == "call":
            tree = CT.build_call_tree(self.db, self.entity, self.down.isChecked(), self.up.isChecked(),
                                      self.depth.value(), self.hide_unres.isChecked(), self.expand.isChecked())
        elif self.relation.currentData() == "members":
            tree = CT.build_member_tree(self.db, self.entity, self.down.isChecked(), self.up.isChecked(),
                                        self.depth.value(), self.hide_unres.isChecked())
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
        elif rel == "members":
            up_n, down_n = "direct base/container", "member"
        else:
            up_n, down_n = "base/container", "member/derived"
        parts = []
        if self.up.isChecked():
            parts.append(f"{n_up} {up_n} nodes")
        if self.down.isChecked():
            parts.append(f"{n_down} {down_n} nodes")
        note = "  ·  node limit reached, reduce the depth" if tree.truncated_nodes else ""
        if self.mode == "call":
            legend = "↻ recursion   … shown elsewhere   ▸ more levels (right-click to expand)"
        elif rel == "members":
            legend = ("+ public  - private  # protected   name() member function   name[] container  "
                      "name* pointer  name? optional   … type expanded elsewhere   ▸ more (right-click to expand)")
        else:
            legend = ("… shown elsewhere   ▸ more (right-click to expand)   △ base class   ◆ owner of a member   "
                      "name[] container  name* pointer  name? optional")
        self.info.setText(f"{self.entity.longname}: " + ", ".join(parts) + note + "   ·   " + legend +
                          "   ·   double-click opens code, right-click for more")

    def _relation_changed(self):
        members = self.relation.currentData() == "members"
        self.members.setVisible(not members)               # every member is a node already
        self.depth.blockSignals(True)
        self.depth.setValue(3 if members else 6)            # member lists grow quickly with depth
        self.depth.blockSignals(False)
        self.rebuild()

    def _rebuild_modify(self):
        mid = self.member.currentData()
        member = self.db.entity(mid) if mid is not None else None
        tree = CT.build_modify_tree(self.db, self.entity, member, self.depth.value(), self.paths.isChecked(),
                                    whole=self.whole.isChecked())
        self.tree = tree
        self._layout()
        what = f"{self.entity.longname}" + (f".{member.name}" if member is not None else "")
        steps = sum(1 for n in tree.nodes() if n.rel == "write")
        if not tree.writers:
            self.info.setText(f"{what}: no writes found. Run Analyze All Files (Ctrl+F5) if the project was "
                              "analyzed with an older version.")
            return
        note = "  ·  node limit reached, reduce the depth" if tree.truncated_nodes else ""
        self.info.setText(f"{what}: modified by {tree.writers} functions, {steps} write steps{note}   ·   "
                          "#n = order of the modifications   = whole object replaced   ↻ recursion   … expanded elsewhere   ▸ depth limit"
                          "   ·   double-click opens code, right-click for more")

    def _layout(self, anchor=None):
        """Lay the tree out and show it; with *anchor*, keep that node where it is on screen."""
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
        view = self.canvas
        before = view.mapFromScene(QPointF(anchor.x, anchor.y)) if anchor is not None else None
        transform = view.transform()
        CT.layout_call_tree(self.tree, measure)
        view.show_tree(self.tree)
        if before is not None:
            view.setTransform(transform)
            # scroll so the anchor node lands on the same viewport position as before
            now = view.mapFromScene(QPointF(anchor.x, anchor.y))
            for bar, delta in ((view.horizontalScrollBar(), now.x() - before.x()),
                               (view.verticalScrollBar(), now.y() - before.y())):
                bar.setValue(bar.value() + delta)

    # ---- interaction ------------------------------------------------------------------------------
    def _activate(self, node):
        if node.rel == "write" and node.call_file:
            self.locationActivated.emit(node.call_file, node.call_line)
        elif node.entity.file_id:
            self.entityActivated.emit(node.entity.id)

    def _menu(self, node, pos):
        m = QMenu(self)
        e = node.entity
        if node.rel == "write":
            for name, line, _whole in node.writes[:12]:
                m.addAction(f"Go to Write of {name} (line {line})",
                            lambda line=line: self.locationActivated.emit(node.call_file, line))
            names = {name for name, _, whole in node.writes if not whole}
            if len(names) == 1 and len(node.writes) == len([w for w in node.writes if not w[2]]) \
                    and self.member.currentData() is None:
                m.addAction(f"Show Only {e.name}", lambda: self.member.setCurrentIndex(
                    max(0, self.member.findData(e.id))))
            m.addSeparator()
        if e.file_id:
            m.addAction("Go to Definition", lambda: self.entityActivated.emit(e.id))
        if node.call_file and node.call_line and self.mode in ("call", "modify") and node.rel != "write":
            m.addAction(f"Go to Call Site (line {node.call_line})",
                        lambda: self.locationActivated.emit(node.call_file, node.call_line))
        if node.call_file and node.call_line and self.mode == "class" and node.rel != "member":
            what = "Member Declaration" if node.rel == "compose" else "Inheritance Declaration"
            m.addAction(f"Go to {what} (line {node.call_line})",
                        lambda: self.locationActivated.emit(node.call_file, node.call_line))
        if e.kind in self.kinds and node.depth != 0 and self.mode != "modify":
            m.addAction(f"Show {self.noun} of This {'Function' if self.mode == 'call' else 'Class'}",
                        lambda: self.set_function(e))
        m.addSeparator()
        if CT.can_grow(self.tree, node):
            m.addAction("Expand One Level", lambda: self._grow(node, 1))
            m.addAction("Expand 3 Levels", lambda: self._grow(node, 3))
            m.addAction("Expand All Levels", lambda: self._grow(node, 10))
        if node.children:
            if any(n.truncated and not n.children for n in node.walk()) and self.mode != "modify":
                m.addAction("Expand Branch One Level Deeper", lambda: self._grow(node, 0))
            m.addAction("Collapse Branch", lambda: self._collapse(node, True))
        if getattr(node, "collapsed", None):
            m.addAction("Expand Branch", lambda: self._collapse(node, False))
        m.addSeparator()
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
        self._layout(anchor=node)

    def _grow(self, node, levels):
        """Expand one node (*levels* > 0) or every cut-off end of its branch by one level (0),
        leaving the depth of the rest of the tree alone."""
        opts = {"hide_unresolved": self.hide_unres.isChecked()}
        if self.mode == "call":
            opts["expand_repeated"] = self.expand.isChecked()
        elif self.relation.currentData() != "members":
            opts["members"] = self.members.isChecked()
        added = CT.grow_branch(self.db, self.tree, node, **opts) if levels == 0 else \
            CT.grow(self.db, self.tree, node, levels, **opts)
        self._layout(anchor=node)
        if not added:
            self.info.setText(f"{node.entity.longname}: nothing more to show.")

    def export(self):
        if self.tree is None:
            return
        name = (self.entity.name if self.entity else "tree").replace(":", "_").replace("~", "")
        prefix = {"call": "calltree", "class": "classtree", "modify": "modtree"}[self.mode]
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
