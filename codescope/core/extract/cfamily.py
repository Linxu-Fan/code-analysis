"""C and C++ extractor."""

from __future__ import annotations

import re

from .. import model as M
from .base import SKIP, BaseExtractor, Visit, simple_type

_WS = re.compile(r"\s+")
_TEMPLATE_ARGS = re.compile(r"<[^<>]*>")


def strip_templates(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _TEMPLATE_ARGS.sub("", s)
    return s


BUILTIN_TYPES = frozenset({"wchar_t", "char8_t", "char16_t", "char32_t"})

DECL_WRAPPERS = frozenset({
    "pointer_declarator", "reference_declarator", "parenthesized_declarator",
    "attributed_declarator", "init_declarator", "array_declarator",
})


class CExtractor(BaseExtractor):
    sep = "::"
    decision_types = frozenset({
        "if_statement", "for_statement", "for_range_loop", "while_statement", "do_statement",
        "case_statement", "conditional_expression", "catch_clause",
    })
    logical_ops = frozenset({"&&", "||", "and", "or"})
    nesting_types = frozenset({"if_statement", "for_statement", "for_range_loop", "while_statement",
                               "do_statement", "switch_statement", "try_statement"})
    statement_types = frozenset({
        "expression_statement", "declaration", "return_statement", "if_statement",
        "for_statement", "for_range_loop", "while_statement", "do_statement",
        "switch_statement", "break_statement", "continue_statement", "goto_statement",
        "try_statement", "throw_statement",
    })

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.access = {}            # class lid -> current access specifier
        self.template_names = []    # stack of sets of template parameter names
        self.typedef_hint = None

    # ---- helpers -------------------------------------------------------------
    def compact(self, node) -> str:
        return _WS.sub(" ", self.text(node)).strip()

    def _scope_kind(self):
        return self.ent(self.cur).kind

    def _in_function(self):
        return any(self.ent(s).kind in M.FUNCTION_KINDS for s in self.scope)

    def _is_template_name(self, name):
        return any(name in s for s in self.template_names)

    def unwrap(self, decl):
        """Descend declarator wrappers; return the innermost declarator node."""
        while decl is not None and decl.type in DECL_WRAPPERS:
            nxt = decl.child_by_field_name("declarator")
            if nxt is None:
                nxt = next((c for c in decl.named_children if c.type not in ("type_qualifier", "attribute_specifier")), None)
            if nxt is None or nxt.id == decl.id:
                break
            decl = nxt
        return decl

    def func_name(self, fdecl):
        """(name, owner, name_node) for a function_declarator's declarator."""
        d = fdecl.child_by_field_name("declarator")
        while d is not None and d.type in DECL_WRAPPERS:
            d = self.unwrap(d)
        if d is None:
            return "", "", None
        if d.type == "qualified_identifier":
            full = strip_templates(self.compact(d)).replace(" ", "")
            owner, _, name = full.rpartition("::")
            last = d
            while last.type == "qualified_identifier" and last.child_by_field_name("name") is not None:
                last = last.child_by_field_name("name")
            self._consume_tree(d)
            return name, owner, last
        if d.type == "template_function":
            n = d.child_by_field_name("name")
            return self.text(n), "", n
        if d.type in ("destructor_name", "operator_name"):
            self._consume_tree(d)
            return self.compact(d).replace(" ", ""), "", d
        return self.text(d), "", d

    def _consume_tree(self, n):
        stack = [n]
        while stack:
            x = stack.pop()
            self.consumed.add(x.start_byte)
            stack.extend(x.named_children)

    def type_ref(self, tnode, src=None):
        """Emit Typed references for the user types appearing in *tnode*."""
        if tnode is None:
            return
        stack = [tnode]
        while stack:
            n = stack.pop()
            t = n.type
            if t == "type_identifier":
                name = self.text(n)
                if not self._is_template_name(name) and name not in BUILTIN_TYPES:
                    self.ref(M.TYPED, name, n, src=src)
                self.consumed.add(n.start_byte)
            elif t == "qualified_identifier":
                full = strip_templates(self.compact(n)).replace(" ", "")
                q, _, name = full.rpartition("::")
                self.ref(M.TYPED, name, n, q, src=src)
                self._consume_tree(n)
                args = [c for c in n.named_children if c.type == "template_type"]
                for a in args:
                    ta = a.child_by_field_name("arguments")
                    if ta is not None:
                        stack.append(ta)
            elif t in ("primitive_type", "sized_type_specifier"):
                continue
            elif t in ("struct_specifier", "class_specifier", "union_specifier", "enum_specifier") \
                    and n.child_by_field_name("body") is None:
                nm = n.child_by_field_name("name")
                if nm is not None:
                    stack.append(nm)
            elif t in ("struct_specifier", "class_specifier", "union_specifier", "enum_specifier"):
                continue        # definitions are handled by their own handler
            else:
                stack.extend(n.named_children)

    def _mods(self, node):
        mods = []
        for c in node.children:
            if c.type in ("storage_class_specifier", "type_qualifier", "virtual", "function_specifier",
                          "virtual_function_specifier"):
                mods.append(self.text(c))
            elif c.type == "explicit_function_specifier":
                mods.append("explicit")
        cls = self.cur
        if self.ent(cls).kind in M.CLASS_KINDS and cls in self.access:
            mods.insert(0, self.access[cls])
        return mods

    # ---- preprocessor -----------------------------------------------------------
    def on_preproc_include(self, node):
        p = node.child_by_field_name("path")
        if p is not None:
            txt = self.text(p).strip()
            system = txt.startswith("<")
            self.ref(M.INCLUDE, txt.strip('<>"'), node, "system" if system else "", src=self.file_lid)
        return SKIP

    def on_preproc_def(self, node):
        n = node.child_by_field_name("name")
        if n is not None:
            val = node.child_by_field_name("value")
            sig = "#define " + self.text(n) + (" " + self.compact(val) if val is not None else "")
            self.add_entity(M.MACRO, self.text(n), node, n, parent=self.file_lid,
                            longname=self.text(n), signature=sig[:160])
        return SKIP

    def on_preproc_function_def(self, node):
        n = node.child_by_field_name("name")
        if n is not None:
            params = node.child_by_field_name("parameters")
            sig = "#define " + self.text(n) + self.text(params)
            self.add_entity(M.MACRO, self.text(n), node, n, parent=self.file_lid,
                            longname=self.text(n), signature=sig[:160], modifiers="function-like")
        return SKIP

    # ---- types ---------------------------------------------------------------
    def on_namespace_definition(self, node):
        n = node.child_by_field_name("name")
        name = self.compact(n).replace(" ", "") if n is not None else "<anonymous>"
        pops = []
        parts = name.split("::")
        for i, part in enumerate(parts):
            lid = self.add_entity(M.NAMESPACE, part, node, n if i == len(parts) - 1 else None)
            pops.append(self.push_scope(lid))
        if n is not None:
            self._consume_tree(n)
        body = node.child_by_field_name("body")

        def done():
            for p in reversed(pops):
                p()
        return Visit([body] if body is not None else [], done)

    def _class(self, node, kind):
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None:
            if name_node is not None:
                self.type_ref(name_node)
            return SKIP
        if name_node is not None:
            if name_node.type == "template_type":
                name_node = name_node.child_by_field_name("name")
            name = strip_templates(self.compact(name_node)).replace(" ", "").split("::")[-1]
        elif self.typedef_hint:
            name = self.typedef_hint
        else:
            name = "<anonymous>"
        self.typedef_hint = None
        sig = self.compact(node).split("{", 1)[0].strip()
        mods = self._mods(node) if self.ent(self.cur).kind in M.CLASS_KINDS else []
        if self.template_names:
            mods.append("template")
        lid = self.add_entity(kind, name, node, name_node, signature=sig[:200], modifiers=" ".join(mods))
        if name_node is not None:
            self._consume_tree(name_node)
        self.access[lid] = "private" if kind == M.CLASS else "public"
        visit = [body]
        for c in node.named_children:
            if c.type == "base_class_clause":
                self._bases(c, lid)
        pop = self.push_scope(lid)
        return Visit(visit, pop)

    def _bases(self, clause, lid):
        for c in clause.named_children:
            if c.type in ("type_identifier", "qualified_identifier", "template_type"):
                n = c.child_by_field_name("name") if c.type == "template_type" else c
                full = strip_templates(self.compact(n)).replace(" ", "")
                q, _, name = full.rpartition("::")
                self.ref(M.INHERIT, name, c, q, src=lid)
                self._consume_tree(c)

    def on_class_specifier(self, node):
        return self._class(node, M.CLASS)

    def on_struct_specifier(self, node):
        return self._class(node, M.STRUCT)

    def on_union_specifier(self, node):
        return self._class(node, M.UNION)

    def on_enum_specifier(self, node):
        body = node.child_by_field_name("body")
        name_node = node.child_by_field_name("name")
        if body is None:
            if name_node is not None:
                self.type_ref(name_node)
            return SKIP
        name = self.text(name_node) if name_node is not None else (self.typedef_hint or "<anonymous>")
        self.typedef_hint = None
        lid = self.add_entity(M.ENUM, name, node, name_node, signature=self.compact(node).split("{")[0].strip())
        for e in body.named_children:
            if e.type == "enumerator":
                en = e.child_by_field_name("name")
                # enumerators are visible in the enclosing scope (C semantics)
                self.add_entity(M.ENUMERATOR, self.text(en), e, en, parent=lid,
                                signature=self.compact(e))
        vals = [e.child_by_field_name("value") for e in body.named_children if e.type == "enumerator"]
        return Visit([v for v in vals if v is not None])

    def on_access_specifier(self, node):
        if self.cur in self.access:
            self.access[self.cur] = self.text(node).strip()
        return SKIP

    def on_type_definition(self, node):
        typ = node.child_by_field_name("type")
        names = []
        for d in node.children_by_field_name("declarator"):
            inner = self.unwrap(d)
            if inner is not None and inner.type in ("type_identifier", "identifier", "primitive_type"):
                names.append((self.text(inner), inner))
            elif inner is not None and inner.type == "function_declarator":
                n = self.unwrap(inner.child_by_field_name("declarator"))
                if n is not None:
                    names.append((self.text(n), n))
        has_body = typ is not None and typ.child_by_field_name("body") is not None
        for name, nn in names:
            tname = ""
            if typ is not None:
                if has_body:
                    tn = typ.child_by_field_name("name")
                    tname = self.text(tn) if tn is not None else name
                else:
                    tname = simple_type(self.text(typ))
            sig = self.compact(node)
            if has_body:
                sig = sig.split("{", 1)[0].strip() + " {...} " + name
            self.add_entity(M.TYPEDEF, name, node, nn, signature=sig[:200], type=tname)
        if typ is not None and typ.type in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier") \
                and typ.child_by_field_name("body") is not None:
            if typ.child_by_field_name("name") is None and names:
                self.typedef_hint = names[0][0]
            return Visit([typ])
        if typ is not None:
            self.type_ref(typ)
        return SKIP

    def on_alias_declaration(self, node):
        n = node.child_by_field_name("name")
        t = node.child_by_field_name("type")
        self.add_entity(M.TYPEDEF, self.text(n), node, n, signature=self.compact(node)[:200],
                        type=simple_type(self.text(t)))
        self.type_ref(t)
        return SKIP

    def on_template_declaration(self, node):
        params = node.child_by_field_name("parameters")
        names = set()
        if params is not None:
            for p in params.named_children:
                for c in p.named_children:
                    if c.type in ("type_identifier", "identifier"):
                        names.add(self.text(c))
                        break
        self.template_names.append(names)

        def done():
            self.template_names.pop()
        rest = [c for c in node.named_children if params is None or c.id != params.id]
        return Visit(rest, done)

    def on_using_declaration(self, node):
        txt = self.compact(node).rstrip(";").strip()
        if txt.startswith("using namespace"):
            self.ref(M.IMPORT, txt[len("using namespace"):].strip(), node, "namespace", src=self.cur)
        return SKIP

    def on_linkage_specification(self, node):
        return None

    # ---- functions ---------------------------------------------------------------
    def _function_kind(self, name, owner):
        sk = self._scope_kind()
        if sk in M.CLASS_KINDS:
            cname = self.ent(self.cur).name
            return M.CONSTRUCTOR if name == cname else M.METHOD
        if owner:
            last = owner.split("::")[-1]
            return M.CONSTRUCTOR if name == last else M.METHOD
        return M.FUNCTION

    def _longname(self, name, owner):
        prefix = self.scope_longname(self.cur)
        q = owner + "::" + name if owner else name
        return prefix + "::" + q if prefix else q

    def on_function_definition(self, node):
        decl = self.unwrap(node.child_by_field_name("declarator"))
        if decl is None or decl.type != "function_declarator":
            return None
        name, owner, name_node = self.func_name(decl)
        if not name:
            return None
        kind = self._function_kind(name, owner)
        body = node.child_by_field_name("body")
        sig = self.compact(node)
        if body is not None:
            sig = sig[:max(0, body.start_byte - node.start_byte)].strip() if False else \
                _WS.sub(" ", self.src[node.start_byte:body.start_byte].decode("utf-8", "replace")).strip()
        mods = self._mods(node)
        if name.startswith("~"):
            mods.append("destructor")
        if self.template_names:
            mods.append("template")
        rtype = node.child_by_field_name("type")
        owner_ln = owner
        prefix = self.scope_longname(self.cur)
        if owner and prefix and self._scope_kind() == M.NAMESPACE:
            owner_ln = prefix + "::" + owner
        lid = self.add_entity(kind, name, node, name_node, longname=self._longname(name, owner),
                              signature=sig[:300], modifiers=" ".join(mods),
                              type=simple_type(self.text(rtype)) if rtype is not None else "",
                              owner=owner_ln)
        if rtype is not None:
            self.type_ref(rtype, src=lid)
        done = self.enter_function(lid)
        extra = []
        params = decl.child_by_field_name("parameters")
        if params is not None:
            for p in params.named_children:
                self._param(p, extra)
        for c in node.named_children:
            if c.type == "field_initializer_list":
                extra.append(c)
        if body is not None:
            extra.append(body)
        return Visit(extra, done)

    def _param(self, p, extra):
        if p.type not in ("parameter_declaration", "optional_parameter_declaration"):
            return
        t = p.child_by_field_name("type")
        self.type_ref(t)
        d = self.unwrap(p.child_by_field_name("declarator"))
        if d is not None and d.type in ("identifier",):
            self.add_entity(M.PARAMETER, self.text(d), p, d,
                            type=simple_type(self.text(t)) if t is not None else "")
        dv = p.child_by_field_name("default_value")
        if dv is not None:
            extra.append(dv)

    def on_parameter_declaration(self, node):
        # catch clauses, lambdas: treat as locals of the current function
        t = node.child_by_field_name("type")
        self.type_ref(t)
        d = self.unwrap(node.child_by_field_name("declarator"))
        if d is not None and d.type == "identifier" and self._scope_kind() in M.FUNCTION_KINDS:
            self.add_entity(M.LOCAL, self.text(d), node, d,
                            type=simple_type(self.text(t)) if t is not None else "")
        return SKIP

    def on_field_initializer(self, node):
        f = next((c for c in node.named_children if c.type == "field_identifier"), None)
        if f is not None:
            self.ref(M.SET, self.text(f), f, "this")
            self.consumed.add(f.start_byte)
        return None

    # ---- declarations ------------------------------------------------------------
    def on_declaration(self, node):
        typ = node.child_by_field_name("type")
        sk = self._scope_kind()
        in_func = sk in M.FUNCTION_KINDS
        mods = self._mods(node)
        tname = simple_type(self.text(typ)) if typ is not None else ""
        visit = []
        if typ is not None and typ.type in ("struct_specifier", "class_specifier", "union_specifier",
                                            "enum_specifier") and typ.child_by_field_name("body") is not None:
            visit.append(typ)
        else:
            self.type_ref(typ)
        for d in node.children_by_field_name("declarator"):
            init = d if d.type == "init_declarator" else None
            inner = self.unwrap(d)
            if inner is None:
                continue
            if init is not None:
                v = init.child_by_field_name("value")
                if v is not None:
                    visit.append(v)
                    if v.type == "argument_list" and typ is not None and in_func:
                        self.ref(M.CREATE, tname, typ)
            if inner.type == "function_declarator":
                if in_func:
                    continue
                name, owner, name_node = self.func_name(inner)
                if not name:
                    continue
                kind = self._function_kind(name, owner)
                sig = self.compact(node).rstrip(";")
                fm = list(mods) + ["decl"]
                if name.startswith("~"):
                    fm.append("destructor")
                self.add_entity(kind, name, node, name_node, longname=self._longname(name, owner),
                                signature=sig[:300], modifiers=" ".join(fm), type=tname, owner=owner)
                params = inner.child_by_field_name("parameters")
                if params is not None:
                    for p in params.named_children:
                        self.type_ref(p.child_by_field_name("type"))
            elif inner.type == "identifier":
                kind = M.LOCAL if in_func else (M.FIELD if sk in M.CLASS_KINDS else M.VARIABLE)
                vm = list(mods)
                if "extern" in vm:
                    vm.append("decl")
                self.add_entity(kind, self.text(inner), d, inner, type=tname,
                                signature=(self.compact(typ) + " " + self.compact(d))[:200] if typ is not None else "",
                                modifiers=" ".join(vm))
            elif inner.type == "qualified_identifier":
                self._consume_tree(inner)
        return Visit(visit)

    def on_field_declaration(self, node):
        typ = node.child_by_field_name("type")
        mods = self._mods(node)
        tname = simple_type(self.text(typ)) if typ is not None else ""
        visit = []
        if typ is not None and typ.type in ("struct_specifier", "class_specifier", "union_specifier",
                                            "enum_specifier") and typ.child_by_field_name("body") is not None:
            visit.append(typ)
            plain_type = None
        else:
            plain_type = typ          # referenced below by each field (or by the class for methods)
        typed_by_field = False
        dv = node.child_by_field_name("default_value")
        for d in node.children_by_field_name("declarator"):
            inner = self.unwrap(d)
            if inner is None:
                continue
            if inner.type == "function_declarator":
                name, owner, name_node = self.func_name(inner)
                if not name:
                    continue
                fm = list(mods) + ["decl"]
                if dv is not None and self.text(dv).strip() == "0":
                    fm.append("pure")
                kind = self._function_kind(name, owner)
                self.add_entity(kind, name, node, name_node, signature=self.compact(node).rstrip(";")[:300],
                                modifiers=" ".join(fm), type=tname)
                params = inner.child_by_field_name("parameters")
                if params is not None:
                    for p in params.named_children:
                        self.type_ref(p.child_by_field_name("type"))
            elif inner.type in ("field_identifier", "identifier"):
                lid = self.add_entity(M.FIELD, self.text(inner), d, inner, type=tname, modifiers=" ".join(mods),
                                      signature=(self.compact(typ) + " " + self.compact(d))[:200]
                                      if typ is not None else "")
                if plain_type is not None:
                    # the field itself references its type: enables composition (has-a) analysis
                    self.type_ref(plain_type, src=lid)
                    typed_by_field = True
        if plain_type is not None and not typed_by_field:
            self.type_ref(plain_type)
        if dv is not None and not any(self.unwrap(d) is not None and self.unwrap(d).type == "function_declarator"
                                      for d in node.children_by_field_name("declarator")):
            visit.append(dv)
        return Visit(visit)

    def on_for_range_loop(self, node):
        d = self.unwrap(node.child_by_field_name("declarator"))
        t = node.child_by_field_name("type")
        self.type_ref(t)
        if d is not None and d.type == "identifier":
            self.add_entity(M.LOCAL, self.text(d), node, d, type=simple_type(self.text(t)) if t is not None else "")
        return Visit([c for c in (node.child_by_field_name("right"), node.child_by_field_name("body")) if c is not None])

    # ---- expressions ----------------------------------------------------------------
    def on_call_expression(self, node):
        fn = node.child_by_field_name("function")
        if fn is None:
            return None
        if fn.type == "identifier":
            self.ref(M.CALL, self.text(fn), fn)
            self.consumed.add(fn.start_byte)
        elif fn.type == "field_expression":
            f = fn.child_by_field_name("field")
            arg = fn.child_by_field_name("argument")
            if f is not None:
                name = self.text(f)
                if f.type == "template_method":
                    name = self.text(f.child_by_field_name("name"))
                self.ref(M.CALL, name, f, self.compact(arg))
                self._consume_tree(f)
        elif fn.type == "qualified_identifier":
            full = strip_templates(self.compact(fn)).replace(" ", "")
            q, _, name = full.rpartition("::")
            self.ref(M.CALL, name, fn, q)
            self._consume_tree(fn)
        elif fn.type == "template_function":
            n = fn.child_by_field_name("name")
            self.ref(M.CALL, self.text(n), n)
            self.consumed.add(n.start_byte)
        return None

    def on_new_expression(self, node):
        t = node.child_by_field_name("type")
        if t is not None:
            n = t.child_by_field_name("name") if t.type == "template_type" else t
            full = strip_templates(self.compact(n)).replace(" ", "")
            q, _, name = full.rpartition("::")
            self.ref(M.CREATE, name, t, q)
            self._consume_tree(t)
        return None

    def on_field_expression(self, node):
        f = node.child_by_field_name("field")
        if f is None or f.start_byte in self.consumed:
            return None
        arg = node.child_by_field_name("argument")
        kind = M.SET if f.start_byte in self.set_nodes else M.USE
        self.ref(kind, self.text(f), f, self.compact(arg))
        self.consumed.add(f.start_byte)
        return None

    def on_qualified_identifier(self, node):
        if node.start_byte in self.consumed:
            return SKIP
        full = strip_templates(self.compact(node)).replace(" ", "")
        q, _, name = full.rpartition("::")
        last = node
        while last.type == "qualified_identifier" and last.child_by_field_name("name") is not None:
            last = last.child_by_field_name("name")
        kind = M.TYPED if last.type in ("type_identifier", "template_type") else M.USE
        self.ref(kind, name, node, q)
        self._consume_tree(node)
        return SKIP

    def _mark_set(self, left):
        if left is None:
            return
        if left.type == "identifier":
            self.set_nodes.add(left.start_byte)
        elif left.type == "field_expression":
            f = left.child_by_field_name("field")
            if f is not None:
                self.set_nodes.add(f.start_byte)

    def on_assignment_expression(self, node):
        self._mark_set(node.child_by_field_name("left"))

    def on_update_expression(self, node):
        self._mark_set(node.child_by_field_name("argument"))

    def on_identifier(self, node):
        if node.start_byte in self.consumed:
            return SKIP
        kind = M.SET if node.start_byte in self.set_nodes else M.USE
        self.ref(kind, self.text(node), node)
        return SKIP

    def on_type_identifier(self, node):
        if node.start_byte in self.consumed:
            return SKIP
        name = self.text(node)
        if not self._is_template_name(name) and name not in BUILTIN_TYPES:
            self.ref(M.TYPED, name, node)
        return SKIP

    def on_sizeof_expression(self, node):
        return None


class CppExtractor(CExtractor):
    pass
