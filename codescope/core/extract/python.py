"""Python extractor."""

from __future__ import annotations

import os

from .. import model as M
from .base import SKIP, BaseExtractor, Visit, handles


def module_name(relpath: str, path: str = "") -> str:
    """Dotted module name. With an absolute *path*, package directories (with __init__.py)
    determine the name, as Python's import system would."""
    if path and os.path.isabs(path):
        d, base = os.path.split(path)
        parts = [os.path.splitext(base)[0]]
        while os.path.exists(os.path.join(d, "__init__.py")):
            d, pkg = os.path.split(d)
            if not pkg:
                break
            parts.insert(0, pkg)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    p = os.path.splitext(relpath.replace("\\", "/"))[0]
    parts = [x for x in p.split("/") if x and x != "."]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class PythonExtractor(BaseExtractor):
    sep = "."
    decision_types = frozenset({
        "if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
        "conditional_expression", "boolean_operator", "if_clause", "case_clause",
    })
    binary_types = frozenset()
    nesting_types = frozenset({"if_statement", "for_statement", "while_statement",
                               "try_statement", "with_statement", "match_statement"})
    statement_types = frozenset({
        "expression_statement", "return_statement", "if_statement", "for_statement",
        "while_statement", "try_statement", "with_statement", "raise_statement",
        "pass_statement", "break_statement", "continue_statement", "assert_statement",
        "delete_statement", "global_statement", "nonlocal_statement", "import_statement",
        "import_from_statement", "match_statement",
    })

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.module = module_name(self.relpath, self.path)
        self.names = {self.file_lid: {}}      # scope lid -> {name: lid}
        self.globals = {}                     # function lid -> set(names)

    def top_prefix(self, name):
        return f"{self.module}.{name}" if self.module else name

    # docstrings are comments
    def is_comment(self, node):
        if node.type == "comment":
            return True
        if node.type == "expression_statement" and node.named_child_count == 1 \
                and node.named_children[0].type == "string":
            parent = node.parent
            if parent is not None and parent.type in ("module", "block"):
                for c in parent.named_children:
                    if c.type == "comment":
                        continue
                    return c.id == node.id
        return False

    # ---- scopes ---------------------------------------------------------------
    def _scope_kind(self):
        return self.ent(self.cur).kind

    def _enclosing_class(self):
        """Class lid when the current scope is a method of a class."""
        e = self.ent(self.cur)
        if e.kind in M.FUNCTION_KINDS and e.parent is not None:
            p = self.ent(e.parent)
            if p.kind == M.CLASS:
                return p.lid
        return None

    def define(self, name, node, name_node=None, kind=None, typ=""):
        """Bind *name* in the current scope, creating an entity on first binding."""
        scope = self.cur
        table = self.names.setdefault(scope, {})
        if name in table:
            return table[name]
        if name in self.globals.get(scope, ()):
            return None
        sk = self._scope_kind()
        if kind is None:
            if sk == M.FILE:
                kind = M.VARIABLE
            elif sk == M.CLASS:
                kind = M.FIELD
            elif sk in M.FUNCTION_KINDS:
                kind = M.LOCAL
            else:
                return None
        lid = self.add_entity(kind, name, node, name_node, type=typ)
        table[name] = lid
        return lid

    # ---- definitions ----------------------------------------------------------
    def on_class_definition(self, node):
        name_node = node.child_by_field_name("name")
        name = self.text(name_node)
        sig = self.first_line(node).rstrip(":")
        lid = self.add_entity(M.CLASS, name, node, name_node, signature=sig)
        self.names.setdefault(self.cur, {})[name] = lid
        self.names[lid] = {}
        sup = node.child_by_field_name("superclasses")
        if sup is not None:
            for c in sup.named_children:
                if c.type in ("identifier", "attribute"):
                    self._inherit(c, lid)
        body = node.child_by_field_name("body")
        pop = self.push_scope(lid)
        return Visit([body] if body is not None else [], pop)

    def _inherit(self, c, lid):
        if c.type == "identifier":
            self.ref(M.INHERIT, self.text(c), c, src=lid)
            self.consumed.add(c.start_byte)
        else:
            obj = c.child_by_field_name("object")
            attr = c.child_by_field_name("attribute")
            self.ref(M.INHERIT, self.text(attr), c, self.text(obj), src=lid)
            self.consumed.add(attr.start_byte)

    def on_function_definition(self, node):
        name_node = node.child_by_field_name("name")
        name = self.text(name_node)
        sk = self._scope_kind()
        kind = M.METHOD if sk == M.CLASS else M.FUNCTION
        params = node.child_by_field_name("parameters")
        ret = node.child_by_field_name("return_type")
        sig = f"def {name}{self.text(params)}" + (f" -> {self.text(ret)}" if ret is not None else "")
        mods = []
        dec = node.parent if node.parent is not None and node.parent.type == "decorated_definition" else None
        if dec is not None:
            for d in dec.named_children:
                if d.type == "decorator":
                    mods.append(self.text(d).lstrip("@").split("(")[0])
        if node.child(0) is not None and node.child(0).type == "async":
            mods.append("async")
        if name.startswith("__") and not name.endswith("__"):
            mods.append("private")
        lid = self.add_entity(kind, name, node, name_node, signature=sig,
                              modifiers=" ".join(mods))
        self.names.setdefault(self.cur, {})[name] = lid
        self.names[lid] = {}
        if ret is not None:
            self._typed(ret, lid)
        done = self.enter_function(lid)
        extra = []
        if params is not None:
            for p in params.named_children:
                self._param(p, kind == M.METHOD, extra)
        body = node.child_by_field_name("body")
        return Visit(extra + ([body] if body is not None else []), done)

    def _param(self, p, is_method, extra):
        name_node = None
        typ = None
        if p.type == "identifier":
            name_node = p
        elif p.type in ("typed_parameter",):
            name_node = next((c for c in p.named_children if c.type == "identifier"), None)
            typ = p.child_by_field_name("type")
        elif p.type in ("default_parameter", "typed_default_parameter"):
            name_node = p.child_by_field_name("name")
            typ = p.child_by_field_name("type")
            val = p.child_by_field_name("value")
            if val is not None:
                extra.append(val)
        elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            name_node = next((c for c in p.named_children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = self.text(name_node)
        if is_method and name in ("self", "cls"):
            self.consumed.add(name_node.start_byte)
            return
        tname = ""
        if typ is not None:
            tname = self.text(typ)
            self._typed(typ, self.cur)
        lid = self.add_entity(M.PARAMETER, name, p, name_node, type=tname)
        self.names.setdefault(self.cur, {})[name] = lid

    def _typed(self, tnode, src):
        stack = [tnode]
        while stack:
            n = stack.pop()
            if n.type == "identifier":
                self.ref(M.TYPED, self.text(n), n, src=src)
                self.consumed.add(n.start_byte)
            elif n.type == "attribute":
                attr = n.child_by_field_name("attribute")
                self.ref(M.TYPED, self.text(attr), n, self.text(n.child_by_field_name("object")), src=src)
                self.consumed.add(attr.start_byte)
                obj = n.child_by_field_name("object")
                self._mark_tree(obj)
            elif n.type == "string":
                continue
            else:
                stack.extend(n.named_children)

    def _mark_tree(self, n):
        stack = [n]
        while stack:
            x = stack.pop()
            if x.type == "identifier":
                self.consumed.add(x.start_byte)
            stack.extend(x.named_children)

    def on_type(self, node):
        self._typed(node, self.cur)
        return SKIP

    # ---- imports --------------------------------------------------------------
    def on_import_statement(self, node):
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                full = self.text(c.child_by_field_name("name"))
                alias = self.text(c.child_by_field_name("alias"))
            else:
                full = self.text(c)
                alias = full.split(".")[0]
            self.ref(M.IMPORT, full, c, alias, src=self.file_lid)
        return SKIP

    def on_import_from_statement(self, node):
        mod = self.text(node.child_by_field_name("module_name")).replace(" ", "")
        names = node.children_by_field_name("name")
        if any(c.type == "wildcard_import" for c in node.children):
            self.ref(M.IMPORT, mod + ".*" if not mod.endswith(".") else mod + "*", node, "*",
                     src=self.file_lid)
        for c in names:
            if c.type == "aliased_import":
                nm = self.text(c.child_by_field_name("name"))
                alias = self.text(c.child_by_field_name("alias"))
            else:
                nm = self.text(c)
                alias = nm
            full = mod + nm if mod.endswith(".") else mod + "." + nm
            self.ref(M.IMPORT, full, c, alias, src=self.file_lid)
        return SKIP

    # ---- statements -------------------------------------------------------------
    def on_global_statement(self, node):
        s = self.globals.setdefault(self.cur, set())
        for c in node.named_children:
            if c.type == "identifier":
                s.add(self.text(c))
                self.consumed.add(c.start_byte)
        return SKIP

    on_nonlocal_statement = on_global_statement

    def _bind_targets(self, left, typ=""):
        stack = [left]
        while stack:
            n = stack.pop()
            t = n.type
            if t == "identifier":
                self.define(self.text(n), n, n, typ=typ)
                self.set_nodes.add(n.start_byte)
            elif t in ("pattern_list", "tuple_pattern", "list_pattern", "parenthesized_expression",
                       "expression_list", "tuple", "list", "list_splat_pattern", "as_pattern_target"):
                stack.extend(n.named_children)
            elif t == "attribute":
                obj = n.child_by_field_name("object")
                attr = n.child_by_field_name("attribute")
                if obj is not None and obj.type == "identifier" and self.text(obj) in ("self", "cls"):
                    cls = self._enclosing_class()
                    if cls is not None:
                        name = self.text(attr)
                        table = self.names.setdefault(cls, {})
                        if name not in table:
                            table[name] = self.add_entity(M.FIELD, name, n, attr, parent=cls)
                self.set_nodes.add(attr.start_byte)

    def on_assignment(self, node):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        typ = node.child_by_field_name("type")
        tname = ""
        if typ is not None:
            tname = self.text(typ)
        elif right is not None and right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and fn.type in ("identifier", "attribute"):
                tname = self.text(fn)
        if left is not None:
            self._bind_targets(left, tname)

    def on_augmented_assignment(self, node):
        left = node.child_by_field_name("left")
        if left is not None and left.type == "identifier":
            self.set_nodes.add(left.start_byte)
        elif left is not None and left.type == "attribute":
            attr = left.child_by_field_name("attribute")
            if attr is not None:
                self.set_nodes.add(attr.start_byte)

    def on_for_statement(self, node):
        left = node.child_by_field_name("left")
        if left is not None:
            self._bind_targets(left)

    def on_for_in_clause(self, node):
        if self._scope_kind() in M.FUNCTION_KINDS:
            left = node.child_by_field_name("left")
            if left is not None:
                self._bind_targets(left)

    def on_as_pattern_target(self, node):
        if self._scope_kind() != M.CLASS:
            self._bind_targets(node)

    def on_named_expression(self, node):
        name = node.child_by_field_name("name")
        if name is not None:
            self._bind_targets(name)

    def on_keyword_argument(self, node):
        n = node.child_by_field_name("name")
        if n is not None:
            self.consumed.add(n.start_byte)

    def on_lambda(self, node):
        params = node.child_by_field_name("parameters")
        if params is not None:
            self._mark_tree(params)
        return None

    # ---- expressions ------------------------------------------------------------
    def on_call(self, node):
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            if self.text(fn) != "super":
                self.ref(M.CALL, self.text(fn), fn)
            self.consumed.add(fn.start_byte)
        elif fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            obj = fn.child_by_field_name("object")
            self.ref(M.CALL, self.text(attr), attr, self.text(obj))
            self.consumed.add(attr.start_byte)
        return None

    def on_attribute(self, node):
        attr = node.child_by_field_name("attribute")
        if attr is None or attr.start_byte in self.consumed:
            return None
        obj = node.child_by_field_name("object")
        kind = M.SET if attr.start_byte in self.set_nodes else M.USE
        self.ref(kind, self.text(attr), attr, self.text(obj))
        self.consumed.add(attr.start_byte)
        return None

    def on_identifier(self, node):
        if node.start_byte in self.consumed:
            return SKIP
        if node.type == "identifier" and self.src[node.start_byte:node.end_byte] in (b"self", b"cls"):
            return SKIP
        kind = M.SET if node.start_byte in self.set_nodes else M.USE
        self.ref(kind, self.text(node), node)
        return SKIP
