"""Syntax + semantic highlighting with tree-sitter, coloured like Visual Studio."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from ..core import model as M
from ..core.languages import get_parser
from . import theme

STRING_TYPES = frozenset({"string_literal", "raw_string_literal", "char_literal", "system_lib_string", "string"})
NUMBER_TYPES = frozenset({"number_literal", "integer", "float"})
KEYWORD_NODES = frozenset({"primitive_type", "true", "false", "null", "nullptr", "this", "none", "auto",
                           "placeholder_type_specifier", "ellipsis"})
KEYWORD_IDENTS = frozenset({
    "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast", "sizeof", "alignof", "decltype",
    "override", "final", "noexcept", "nullptr", "this", "true", "false", "NULL", "co_await", "co_return",
    "co_yield", "typeid",
})
PY_KEYWORD_IDENTS = frozenset({"self", "cls"})
CALL_PARENTS = frozenset({"call_expression", "call"})
FUNC_DECL_PARENTS = frozenset({"function_declarator"})
QUALIFIERS = frozenset({"qualified_identifier", "template_function", "template_method", "scoped_identifier",
                        "destructor_name"})

SEMANTIC_KIND = {}
for _k in M.CLASS_KINDS | {M.TYPEDEF, M.UNRESOLVED_TYPE}:
    SEMANTIC_KIND[_k] = "type"
for _k in M.FUNCTION_KINDS | {M.UNRESOLVED_FUNCTION}:
    SEMANTIC_KIND[_k] = "function"
SEMANTIC_KIND[M.MACRO] = "macro"
SEMANTIC_KIND[M.ENUMERATOR] = "enumerator"
SEMANTIC_KIND[M.NAMESPACE] = "namespace"


def _is_word(s):
    return s[:1].isalpha() or s[:1] == "_"


class TreeSitterHighlighter(QSyntaxHighlighter):
    """Parses the whole document with tree-sitter and colours tokens per block.

    Semantic tokens (from the analysis database) are layered on top so that a
    name is coloured by what it resolves to, like Visual Studio's IntelliSense
    colouring.
    """

    def __init__(self, doc, lang):
        super().__init__(doc)
        self.lang = lang
        self.parser = get_parser(lang)
        self.spans = {}
        self.semantic = []          # (line, col, name, category)
        self.semantic_spans = {}
        self.semantic_enabled = True
        self.type_names = frozenset()
        self.macro_names = frozenset()
        self.formats = {}
        self._build_formats()
        self.relex()

    # ---- formats --------------------------------------------------------------------------
    def _build_formats(self):
        th = theme.code_theme()
        self.formats.clear()
        for key in ("keyword", "type", "function", "string", "number", "comment", "preproc", "macro",
                    "namespace", "enumerator", "operator", "escape", "doc_comment"):
            f = QTextCharFormat()
            f.setForeground(QColor(th[key]))
            self.formats[key] = f

    def refresh_theme(self):
        self._build_formats()
        self.rehighlight()

    # ---- syntax ---------------------------------------------------------------------------------
    def relex(self):
        text = self.document().toPlainText()
        data = text.encode("utf-8")
        tree = self.parser.parse(data)
        lines = text.split("\n")
        byte_lines = data.split(b"\n")
        col_maps = {}

        def char_col(row, bcol):
            if row >= len(lines):
                return bcol
            if len(byte_lines[row]) == len(lines[row]):
                return bcol
            m = col_maps.get(row)
            if m is None:
                # cumulative byte offset at the start of each character
                offs = [0]
                for ch in lines[row]:
                    offs.append(offs[-1] + len(ch.encode("utf-8")))
                m = offs
                col_maps[row] = m
            lo, hi = 0, len(m) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if m[mid] <= bcol:
                    lo = mid
                else:
                    hi = mid - 1
            return lo

        spans = {}

        def add(node, key):
            (sr, sc), (er, ec) = node.start_point, node.end_point
            for row in range(sr, er + 1):
                a = char_col(row, sc) if row == sr else 0
                b = char_col(row, ec) if row == er else len(lines[row]) if row < len(lines) else 0
                if b > a:
                    spans.setdefault(row, []).append((a, b - a, key))

        src = data
        stack = [tree.root_node]
        python = self.lang == "python"
        while stack:
            n = stack.pop()
            t = n.type
            if t == "comment":
                txt = src[n.start_byte:n.start_byte + 3]
                add(n, "doc_comment" if txt in (b"///", b"/**") else "comment")
                continue
            if t in STRING_TYPES:
                add(n, "string")
                if python:
                    # f-string interpolations keep their own colours
                    for c in n.children:
                        if c.type == "interpolation":
                            stack.append(c)
                continue
            if t in NUMBER_TYPES:
                add(n, "number")
                continue
            if t in KEYWORD_NODES and n.child_count == 0:
                add(n, "keyword")
                continue
            if t == "preproc_arg":
                continue
            if n.child_count == 0:
                key = self._leaf_key(n, src, python)
                if key:
                    add(n, key)
                continue
            if t in ("preproc_def", "preproc_function_def"):
                name = n.child_by_field_name("name")
                if name is not None:
                    add(name, "macro")
            stack.extend(n.children)
        self.spans = spans
        self.rehighlight()

    def _leaf_key(self, n, src, python):
        t = n.type
        if not n.is_named:
            txt = src[n.start_byte:n.end_byte].decode("utf-8", "replace")
            if txt.startswith("#"):
                return "preproc"
            if _is_word(txt):
                return "keyword"
            return None
        if t == "type_identifier":
            if self.macro_names and src[n.start_byte:n.end_byte].decode("utf-8", "replace") in self.macro_names:
                return "macro"
            return "type"
        if t == "namespace_identifier":
            txt = src[n.start_byte:n.end_byte].decode("utf-8", "replace")
            return "type" if txt in self.type_names else "namespace"
        if t in ("identifier", "field_identifier", "property_identifier"):
            txt = src[n.start_byte:n.end_byte].decode("utf-8", "replace")
            if not python and txt in KEYWORD_IDENTS:
                return "keyword"
            if not python and txt in self.macro_names:
                return "macro"
            p = n.parent
            if p is None:
                return None
            if python:
                if p.type == "function_definition" and p.child_by_field_name("name") == n:
                    return "function"
                if p.type == "class_definition" and p.child_by_field_name("name") == n:
                    return "type"
                if p.type == "call" and p.child_by_field_name("function") == n:
                    return "function"
                if p.type == "attribute" and p.child_by_field_name("attribute") == n and \
                        p.parent is not None and p.parent.type == "call" and p.parent.child_by_field_name("function") == p:
                    return "function"
                if p.type == "decorator" or (p.type == "attribute" and p.parent is not None and p.parent.type == "decorator"):
                    return "macro"
                return None
            # walk up through qualified names to see whether this names a function
            child, up = n, p
            while up is not None and up.type in QUALIFIERS:
                if up.type in ("qualified_identifier", "scoped_identifier") and up.child_by_field_name("name") != child:
                    return None          # this is a scope part, e.g. "math" in math::maxValue
                child, up = up, up.parent
            if up is None:
                return None
            if up.type in FUNC_DECL_PARENTS and up.child_by_field_name("declarator") == child:
                return "function"
            if up.type in CALL_PARENTS and up.child_by_field_name("function") == child:
                return "function"
            if up.type == "field_expression" and up.child_by_field_name("field") == child and \
                    up.parent is not None and up.parent.type in CALL_PARENTS and \
                    up.parent.child_by_field_name("function") == up:
                return "function"
            return None
        return None

    # ---- semantic layer ----------------------------------------------------------------------------
    def set_semantic(self, tokens):
        """tokens: [(line (1-based), col (1-based), name, entity kind)]"""
        self.semantic = tokens
        self._rebuild_semantic()
        self.relex()

    def _rebuild_semantic(self):
        spans = {}
        doc = self.document()
        for line, col, name, kind in self.semantic:
            cat = SEMANTIC_KIND.get(kind)
            if cat is None or not name or name in KEYWORD_IDENTS:
                continue
            blk = doc.findBlockByNumber(line - 1)
            if not blk.isValid():
                continue
            text = blk.text()
            i = text.find(name, max(0, col - 1))
            while i >= 0:
                before = text[i - 1] if i > 0 else " "
                after = text[i + len(name)] if i + len(name) < len(text) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    break
                i = text.find(name, i + 1)
            if i >= 0:
                spans.setdefault(line - 1, []).append((i, len(name), cat))
        self.semantic_spans = spans

    def disable_semantic(self):
        if self.semantic_enabled:
            self.semantic_enabled = False
            self.rehighlight()

    def highlightBlock(self, text):
        n = self.currentBlock().blockNumber()
        for start, length, key in self.spans.get(n, ()):
            self.setFormat(start, length, self.formats[key])
        if self.semantic_enabled:
            for start, length, key in self.semantic_spans.get(n, ()):
                self.setFormat(start, length, self.formats[key])
