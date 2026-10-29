"""Base class for tree-sitter driven entity / reference / metric extraction."""

from __future__ import annotations

import math
import os
import re
from collections import Counter

from .. import model as M
from ..model import FileResult, XEntity, XRef

SKIP = object()            # handler result: do not visit children


class Visit:
    """Handler result: visit only *children* then call *on_exit*."""
    __slots__ = ("children", "on_exit")

    def __init__(self, children=None, on_exit=None):
        self.children = children
        self.on_exit = on_exit


OPERAND_LEAVES = frozenset({
    "number", "integer", "float", "true", "false", "null", "none", "None", "nil",
    "string_content", "string_fragment", "escape_sequence", "char_literal",
    "this", "self", "super", "character_literal", "string_literal", "raw_string_literal",
    "interpreted_string_literal", "rune_literal", "imaginary_literal", "undefined",
    "regex_pattern", "nullptr",
})


class _FuncStats:
    __slots__ = ("lid", "cyclo", "nest", "max_nest", "stmts", "returns", "ops", "opnds")

    def __init__(self, lid):
        self.lid = lid
        self.cyclo = 1
        self.nest = 0
        self.max_nest = 0
        self.stmts = 0
        self.returns = 0
        self.ops = Counter()
        self.opnds = Counter()


class BaseExtractor:
    lang = ""
    sep = "."
    comment_types = frozenset({"comment"})
    decision_types = frozenset()
    logical_ops = frozenset({"&&", "||"})
    binary_types = frozenset({"binary_expression"})
    nesting_types = frozenset()
    statement_types = frozenset()
    return_types = frozenset({"return_statement"})
    function_kinds = M.FUNCTION_KINDS

    def __init__(self, path: str, lang: str, source: bytes, tree, relpath: str = ""):
        self.path = path
        self.relpath = relpath or os.path.basename(path)
        self.lang = lang
        self.src = source
        self.tree = tree
        self.entities: list[XEntity] = []
        self.refs: list[XRef] = []
        self.metrics: dict[int, dict] = {}
        self._ref_keys = set()
        self.scope: list[int] = []
        self.funcs: list[_FuncStats] = []
        self.consumed: set[int] = set()     # start bytes of identifiers already handled
        self.set_nodes: set[int] = set()    # start bytes of identifiers being assigned
        self.code_rows: set[int] = set()
        self.comment_rows: set[int] = set()
        self.parse_errors = 0
        self.handlers = {}
        for attr in dir(self):
            if attr.startswith("on_"):
                fn = getattr(self, attr)
                for t in getattr(fn, "_node_types", (attr[3:],)):
                    self.handlers[t] = fn
        name = os.path.basename(path)
        root = tree.root_node
        self.file_lid = self._new_entity(M.FILE, name, self.relpath, None,
                                         root.start_point, root.end_point)
        self.scope.append(self.file_lid)

    # ---- helpers ------------------------------------------------------------
    def text(self, node) -> str:
        if node is None:
            return ""
        return self.src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def first_line(self, node, limit=160) -> str:
        t = self.text(node).split("\n", 1)[0].strip()
        return t[:limit]

    @property
    def cur(self) -> int:
        return self.scope[-1]

    def ent(self, lid) -> XEntity:
        return self.entities[lid]

    def scope_longname(self, lid) -> str:
        e = self.entities[lid]
        return "" if e.kind == M.FILE else e.longname

    def _new_entity(self, kind, name, longname, parent, start, end, **kw) -> int:
        lid = len(self.entities)
        self.entities.append(XEntity(lid, kind, name, longname, parent,
                                     start[0] + 1, start[1] + 1, end[0] + 1, end[1] + 1, **kw))
        return lid

    def add_entity(self, kind, name, node, name_node=None, parent=None, longname=None,
                   **kw) -> int:
        if parent is None:
            parent = self.cur
        if longname is None:
            prefix = self.scope_longname(parent)
            longname = prefix + self.sep + name if prefix else self.top_prefix(name)
        if name_node is not None:
            self.consumed.add(name_node.start_byte)
        start = (name_node or node).start_point
        lid = self._new_entity(kind, name, longname, parent, start, node.end_point, **kw)
        # remember the full extent start for line metrics
        self.metrics.setdefault(lid, {})["_start"] = node.start_point[0] + 1
        return lid

    def top_prefix(self, name):
        return name

    def ref(self, kind, name, node, qualifier="", src=None):
        if not name:
            return
        if src is None:
            src = self.cur
        line = node.start_point[0] + 1
        key = (kind, src, name, qualifier, line)
        if key in self._ref_keys:
            return
        self._ref_keys.add(key)
        self.refs.append(XRef(kind, src, name, qualifier, line, node.start_point[1] + 1))

    def push_scope(self, lid):
        self.scope.append(lid)

        def pop():
            self.scope.pop()
        return pop

    def enter_function(self, lid):
        """Push *lid* as both scope and metric accumulator; returns exit callback."""
        self.scope.append(lid)
        self.funcs.append(_FuncStats(lid))

        def done():
            st = self.funcs.pop()
            self.scope.pop()
            self._finish_function(st)
        return done

    def _finish_function(self, st: _FuncStats):
        m = self.metrics.setdefault(st.lid, {})
        m["Cyclomatic"] = st.cyclo
        m["MaxNesting"] = st.max_nest
        m["CountStmt"] = st.stmts
        m["CountReturn"] = st.returns
        n1, n2 = len(st.ops), len(st.opnds)
        N1, N2 = sum(st.ops.values()), sum(st.opnds.values())
        vocab, length = n1 + n2, N1 + N2
        volume = length * math.log2(vocab) if vocab > 1 else 0.0
        difficulty = (n1 / 2.0) * (N2 / n2) if n2 else 0.0
        m["HalsteadVolume"] = round(volume, 2)
        m["HalsteadDifficulty"] = round(difficulty, 2)
        m["HalsteadEffort"] = round(volume * difficulty, 2)

    # ---- traversal ----------------------------------------------------------
    def is_comment(self, node) -> bool:
        return node.type in self.comment_types

    def is_default_case(self, node) -> bool:
        c = node.child(0)
        return c is not None and c.type == "default"

    def operator_text(self, node):
        op = node.child_by_field_name("operator")
        if op is None and node.child_count == 3:
            op = node.child(1)
        return self.text(op) if op is not None else ""

    def run(self) -> FileResult:
        """Walk the whole tree.

        Every node is visited exactly once for metrics.  Handlers decide which
        children are *dispatched* (handlers invoked); the remaining children are
        still walked silently so that line counts and Halstead data stay exact.
        Nodes returned by a handler that are not direct children are dispatched
        without metrics (they are covered by their real parent's walk).
        """
        root = self.tree.root_node
        stack = [(root, None, True, True)]
        handlers = self.handlers
        funcs = self.funcs
        decision, nesting = self.decision_types, self.nesting_types
        stmts, returns = self.statement_types, self.return_types
        binary = self.binary_types
        code_rows, comment_rows = self.code_rows, self.comment_rows
        push = stack.append
        while stack:
            node, cb, dispatch, metrics = stack.pop()
            if node is None:
                cb()
                continue
            t = node.type
            if metrics:
                if self.is_comment(node):
                    for r in range(node.start_point[0], node.end_point[0] + 1):
                        comment_rows.add(r)
                    continue
                if t == "ERROR":
                    self.parse_errors += 1
            nest_exit = None
            if metrics and funcs:
                f = funcs[-1]
                if t in decision and not self.is_default_case(node):
                    f.cyclo += 1
                elif t in binary and self.operator_text(node) in self.logical_ops:
                    f.cyclo += 1
                if t in nesting:
                    f.nest += 1
                    if f.nest > f.max_nest:
                        f.max_nest = f.nest

                    def nest_exit(f=f):
                        f.nest -= 1
                if t in stmts:
                    f.stmts += 1
                if t in returns:
                    f.returns += 1
            if metrics and node.child_count == 0 and node.end_byte > node.start_byte:
                sr, er = node.start_point[0], node.end_point[0]
                code_rows.add(sr)
                if er != sr:
                    code_rows.update(range(sr + 1, er + 1))
                if funcs:
                    tx = self.src[node.start_byte:node.end_byte]
                    if "identifier" in t or "literal" in t or t in OPERAND_LEAVES:
                        funcs[-1].opnds[tx] += 1
                    else:
                        funcs[-1].ops[tx] += 1
            res = None
            if dispatch:
                h = handlers.get(t)
                if h is not None:
                    res = h(node)
            if nest_exit is not None:
                push((None, nest_exit, False, False))
            children = node.children
            if res is None:
                for c in reversed(children):
                    push((c, None, dispatch, metrics))
                continue
            if res is SKIP:
                if metrics:
                    for c in reversed(children):
                        push((c, None, False, True))
                continue
            listed = None
            if isinstance(res, Visit):
                if res.on_exit is not None:
                    push((None, res.on_exit, False, False))
                listed = res.children
            elif callable(res):
                push((None, res, False, False))
            if listed is None:
                for c in reversed(children):
                    push((c, None, True, metrics))
                continue
            direct = {c.id for c in children}
            wanted = {c.id for c in listed}
            for c in reversed(listed):
                if c.id not in direct:
                    push((c, None, True, False))
            for c in reversed(children):
                if c.id in wanted:
                    push((c, None, True, metrics))
                elif metrics:
                    push((c, None, False, True))
        return self._result()

    # ---- results -----------------------------------------------------------
    def _result(self) -> FileResult:
        lines = self.src.split(b"\n")
        if lines and lines[-1] == b"" and len(lines) > 1:
            lines.pop()
        n = len(lines)
        code_p = [0] * (n + 1)
        com_p = [0] * (n + 1)
        blank_p = [0] * (n + 1)
        for i in range(n):
            code_p[i + 1] = code_p[i] + (1 if i in self.code_rows else 0)
            com_p[i + 1] = com_p[i] + (1 if i in self.comment_rows else 0)
            blank_p[i + 1] = blank_p[i] + (1 if not lines[i].strip() else 0)

        def line_metrics(m, a, b):          # 1-based inclusive rows
            a = max(1, a)
            b = min(n, b)
            if b < a:
                b = a - 1
            code = code_p[b] - code_p[a - 1]
            com = com_p[b] - com_p[a - 1]
            m["CountLine"] = b - a + 1
            m["CountLineCode"] = code
            m["CountLineComment"] = com
            m["CountLineBlank"] = blank_p[b] - blank_p[a - 1]
            m["RatioCommentToCode"] = round(com / code, 2) if code else 0.0

        children = {}
        for e in self.entities:
            if e.parent is not None:
                children.setdefault(e.parent, []).append(e)
        file_m = self.metrics.setdefault(self.file_lid, {})
        line_metrics(file_m, 1, n)
        cyclos = []
        nfunc = nclass = 0
        for e in self.entities:
            if e.kind in self.function_kinds and "decl" in e.modifiers.split():
                self.metrics.pop(e.lid, None)
            elif e.kind in self.function_kinds:
                nfunc += 1
                m = self.metrics.setdefault(e.lid, {})
                line_metrics(m, m.pop("_start", e.line), e.end_line)
                m["CountParams"] = sum(1 for c in children.get(e.lid, ()) if c.kind == M.PARAMETER)
                m.setdefault("Cyclomatic", 1)
                cyclos.append(m["Cyclomatic"])
                vol = m.get("HalsteadVolume", 0.0)
                loc = max(m["CountLineCode"], 1)
                mi = 171 - 5.2 * math.log(max(vol, 1.0)) - 0.23 * m["Cyclomatic"] - 16.2 * math.log(loc)
                m["MaintainabilityIndex"] = round(max(0.0, mi * 100.0 / 171.0), 1)
            elif e.kind in M.CLASS_KINDS or e.kind in (M.NAMESPACE, M.PACKAGE, M.MODULE):
                if e.kind in M.CLASS_KINDS:
                    nclass += 1
                m = self.metrics.setdefault(e.lid, {})
                line_metrics(m, m.pop("_start", e.line), e.end_line)
            else:
                m = self.metrics.get(e.lid)
                if m is not None:
                    m.pop("_start", None)
                    if not m:
                        del self.metrics[e.lid]
        if self.lang in ("c", "cpp"):
            # preprocessor directive lines (including continuation lines)
            pre = 0
            cont = False
            for i, raw in enumerate(lines):
                st = raw.strip()
                if (cont or st.startswith(b"#")) and i not in self.comment_rows:
                    pre += 1
                cont = (cont or st.startswith(b"#")) and st.endswith(b"\\")
            file_m["CountLinePreprocessor"] = pre
        file_m["CountDeclFunction"] = nfunc
        file_m["CountDeclClass"] = nclass
        file_m["SumCyclomatic"] = sum(cyclos)
        file_m["MaxCyclomatic"] = max(cyclos) if cyclos else 0
        file_m["AvgCyclomatic"] = round(sum(cyclos) / len(cyclos), 2) if cyclos else 0
        return FileResult(self.path, self.lang, self.entities, self.refs, self.metrics,
                          parse_errors=self.parse_errors)


def handles(*types):
    """Decorator registering a handler method for several node types."""
    def deco(fn):
        fn._node_types = types
        return fn
    return deco


_TYPE_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPE_NOISE = frozenset({
    "const", "volatile", "static", "unsigned", "signed", "struct", "class", "union", "enum",
    "typename", "mutable", "register", "extern", "inline", "final", "readonly", "ref", "out",
    "in", "params", "var", "let", "constexpr", "auto", "std",
})


def simple_type(text: str) -> str:
    """Reduce a declared type to its principal identifier: 'const std::vector<Foo>*' -> 'vector'."""
    if not text:
        return ""
    t = text.split("<", 1)[0]
    t = t.replace("::", ".")
    words = [w for w in _TYPE_WORD.findall(t) if w not in _TYPE_NOISE]
    return words[-1] if words else ""
