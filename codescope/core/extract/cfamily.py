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
# member functions of standard containers and smart pointers that modify the object
MUTATORS = frozenset({
    "push_back", "push_front", "emplace_back", "emplace_front", "emplace", "emplace_hint", "insert",
    "insert_or_assign", "try_emplace", "erase", "clear", "pop_back", "pop_front", "resize", "assign",
    "swap", "reset", "release", "fill", "setZero", "setOnes", "setConstant", "setIdentity", "setRandom",
    "conservativeResize", "append", "replace", "push", "pop", "merge", "splice", "sort", "unique",
    "reverse", "remove", "remove_if", "store", "exchange", "fetch_add", "fetch_sub",
})

DECL_WRAPPERS = frozenset({
    "pointer_declarator", "reference_declarator", "parenthesized_declarator",
    "attributed_declarator", "init_declarator", "array_declarator",
})
# functions that create an object of their template argument
FACTORIES = frozenset({"make_unique", "make_shared", "make_optional", "allocate_shared"})
# nodes that end the scope of a local variable
SCOPE_NODES = frozenset({"compound_statement", "for_statement", "for_range_loop", "if_statement", "while_statement",
                         "switch_statement", "catch_clause", "translation_unit"})
_LEADING_NAME = re.compile(r"[A-Za-z_]\w*")


class _Alias:
    """A local reference or pointer to other data: writing through it writes that data."""
    __slots__ = ("targets", "quals", "pointer", "auto", "start", "end")

    def __init__(self, pointer, auto, start, end):
        self.targets = []           # expression nodes it refers to; empty: a plain local that hides an outer alias
        self.quals = []             # the same expressions as text, for names written through an auto alias
        self.pointer = pointer
        self.auto = auto            # declared with auto: only the expression tells its type
        self.start = start
        self.end = end


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
        self.ref_returns = set()    # functions that return a non-const reference
        self.lambdas = {}           # (function lid, local name) -> writable parameters of a lambda
        self.aliases = {}           # (function lid, local name) -> [_Alias], newest last

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

    def _is_const(self, node):
        """True if *node* (a parameter or function) has a 'const' on its base type."""
        return any(c.type == "type_qualifier" and self.text(c) == "const" for c in node.children)

    def _writable_params(self, params):
        """Parameters through which a function can change the caller's object: ['r3', 'p5'].

        r: non-const lvalue reference.  p: pointer to non-const.  T&& is left out: it binds to
        temporaries and moved objects, and template forwarding references cannot be told apart.
        """
        out = []
        if params is None:
            return out
        items = [p for p in params.named_children if p.type != "comment"]
        for i, p in enumerate(items):
            if p.type not in ("parameter_declaration", "optional_parameter_declaration") or self._is_const(p):
                continue
            d = p.child_by_field_name("declarator")
            while d is not None and d.type in ("parenthesized_declarator", "attributed_declarator"):
                d = d.named_children[0] if d.named_children else None
            if d is None:
                continue
            if d.type in ("reference_declarator", "abstract_reference_declarator"):
                if not any(c.type == "&&" for c in d.children):
                    out.append(f"r{i}")
            elif d.type in ("pointer_declarator", "abstract_pointer_declarator"):
                out.append(f"p{i}")
        return out

    def _type_parts(self, typ):
        """(name, qualifier) of a class-like type node, or None for auto, int, template parameters, ..."""
        if typ is None:
            return None
        t = typ.type
        if t == "type_identifier":
            name, q = self.text(typ), ""
        elif t == "template_type":
            name, q = self.text(typ.child_by_field_name("name")), ""
        elif t == "qualified_identifier":
            q, _, name = strip_templates(self.compact(typ)).replace(" ", "").rpartition("::")
        else:
            return None
        if not name or name in BUILTIN_TYPES or self._is_template_name(name):
            return None
        return name, q

    def _object_ref(self, typ, node, var=""):
        """Record that an object of type *typ* is created at *node*."""
        parts = self._type_parts(typ)
        if parts is not None:
            self.ref(M.OBJECT, parts[0], node, parts[1], detail=var)

    # ---- local aliases -------------------------------------------------------------
    def _is_lvalue_ref(self, decl, auto):
        """True for 'T& x' and 'auto&& x'.  'T&& x' binds to temporaries, so it refers to no named data."""
        return decl is not None and decl.type == "reference_declarator" \
            and (auto or not any(c.type == "&&" for c in decl.children))

    def _alias_targets(self, value, pointer):
        """Expressions a new reference or pointer refers to; a?b:c gives both, a pointer copy what it points at."""
        if value is None:
            return []
        t = value.type
        if t in ("parenthesized_expression", "initializer_list", "argument_list"):
            items = [c for c in value.named_children if c.type != "comment"]
            return self._alias_targets(items[0], pointer) if len(items) == 1 else []
        if t == "conditional_expression":
            return self._alias_targets(value.child_by_field_name("consequence"), pointer) + \
                self._alias_targets(value.child_by_field_name("alternative"), pointer)
        if pointer:
            if t == "pointer_expression" and self.text(value.child_by_field_name("operator")) == "&":
                return [value.child_by_field_name("argument")]
            if t == "identifier":
                a = self._alias_of(self.text(value), value.start_byte)
                return list(a.targets) if a is not None and a.pointer else []
            return []
        return [value] if t in ("identifier", "field_expression", "subscript_expression", "call_expression",
                                "pointer_expression") else []

    def _add_alias_targets(self, alias, targets, suffix=""):
        for target in targets:
            alias.targets.append(target)
            alias.quals += [q + suffix for q in self._quals(self.compact(target), target.start_byte)]

    def _note_local(self, node, decl, typ, name_node, value):
        """Remember a local declared by *decl*: an alias, or a plain local that hides an outer alias."""
        key = (self.cur, self.text(name_node))
        auto = typ is not None and typ.type == "placeholder_type_specifier"
        d = decl.child_by_field_name("declarator") if decl.type == "init_declarator" else decl
        pointer = d is not None and d.type == "pointer_declarator"
        writable = not self._is_const(node) and (pointer or self._is_lvalue_ref(d, auto))
        if not writable and key not in self.aliases:
            return
        scope = node.parent
        while scope is not None and scope.type not in SCOPE_NODES:
            scope = scope.parent
        alias = _Alias(pointer and writable, auto, node.start_byte, scope.end_byte if scope is not None else node.end_byte)
        if writable:
            self._add_alias_targets(alias, self._alias_targets(value, pointer))
        self.aliases.setdefault(key, []).append(alias)

    def _alias_entry(self, name, at):
        for a in reversed(self.aliases.get((self.cur, name), ())):
            if a.start <= at < a.end:
                return a
        return None

    def _alias_of(self, name, at):
        """The alias called *name* at byte *at*, if it refers to something."""
        a = self._alias_entry(name, at)
        return a if a is not None and a.targets else None

    def _quals(self, text, at):
        """Qualifiers for a name written through *text*: an auto alias at its start becomes what it refers to.

        After ``auto& j = b.items[0];`` the write ``j.mass = 1`` has qualifier ``b.items[0]``: the
        declared type of j says nothing, but the expression leads to the member.
        """
        m = _LEADING_NAME.match(text)
        if m is not None and not text.startswith("::", m.end()):
            a = self._alias_of(m.group(), at)
            if a is not None and a.auto:
                return [q + text[m.end():] for q in a.quals]
        return [text]

    def _alias_root(self, left):
        """(alias, its name node, how) if writing to *left* writes through a local alias, else None.

        how is "" when the data itself is written, "part" when a member or an element of it is.
        Writing a pointer variable itself (p = &x) moves the pointer and writes nothing else.
        """
        steps = 0
        while left is not None:
            t = left.type
            if t == "parenthesized_expression":
                left = left.named_children[0] if left.named_children else None
                continue
            arrow = t == "field_expression" and self.text(left.child_by_field_name("operator")) == "->"
            deref = t == "pointer_expression" and self.text(left.child_by_field_name("operator")) == "*"
            if arrow or deref or t == "subscript_expression":
                arg = left.child_by_field_name("argument")
                if arg is not None and arg.type == "identifier":
                    a = self._alias_of(self.text(arg), arg.start_byte)
                    if a is not None and a.pointer:
                        return a, arg, "" if deref and not steps else "part"
                if arrow or deref:
                    return None           # another pointer: the target of the alias does not change
                steps, left = steps + 1, arg
            elif t == "field_expression":
                steps, left = steps + 1, left.child_by_field_name("argument")
            elif t == "identifier":
                a = self._alias_of(self.text(left), left.start_byte)
                return None if a is None or a.pointer else (a, left, "part" if steps else "")
            else:
                return None
        return None

    def _alias_names(self, alias, seen=None):
        """(name, qualifier) of each name reached through *alias*; a call in it gives (None, call node)."""
        seen = seen if seen is not None else set()
        if id(alias) in seen:
            return
        seen.add(id(alias))
        for target in alias.targets:
            for name, obj in self._write_chain(target):
                if name is None:
                    yield None, obj
                    continue
                for q in self._quals(self.compact(obj) if obj is not None else "", name.start_byte):
                    yield self.text(name), q
            inner = self._alias_root(target)
            if inner is not None:
                yield from self._alias_names(inner[0], seen)

    def _write_call_ref(self, call, at, local=""):
        """f(x) = value, or an alias of f(x) written at *at*: writes what the returned reference refers to."""
        callee = self._callee(call.child_by_field_name("function"))
        args = call.child_by_field_name("arguments")
        if callee is not None and args is not None:
            count = sum(1 for a in args.named_children if a.type != "comment")
            for q in self._quals(callee[1], call.start_byte):
                self.ref(M.WRITE_CALL, callee[0], at, q, detail=f"{count}\t{local}")

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
        params = decl.child_by_field_name("parameters")
        token = M.out_token(self._writable_params(params))
        if token:
            mods.append(token)
        rtype = node.child_by_field_name("type")
        owner_ln = owner
        prefix = self.scope_longname(self.cur)
        if owner and prefix and self._scope_kind() == M.NAMESPACE:
            owner_ln = prefix + "::" + owner
        lid = self.add_entity(kind, name, node, name_node, longname=self._longname(name, owner),
                              signature=sig[:300], modifiers=" ".join(mods),
                              type=simple_type(self.text(rtype)) if rtype is not None else "",
                              owner=owner_ln)
        outer = node.child_by_field_name("declarator")
        if outer.type == "reference_declarator" and not self._is_const(node) \
                and not any(c.type == "&&" for c in outer.children):
            self.ref_returns.add(lid)
        if rtype is not None:
            self.type_ref(rtype, src=lid)
        done = self.enter_function(lid)
        extra = []
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
    def _holds_object(self, decl):
        """False for declarators of pointers and references: those do not create an object."""
        while decl is not None and decl.type in DECL_WRAPPERS:
            if decl.type in ("pointer_declarator", "reference_declarator"):
                return False
            decl = decl.child_by_field_name("declarator") or \
                next((c for c in decl.named_children if c.type not in ("type_qualifier", "attribute_specifier")), None)
        return True

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
                    if v.type == "lambda_expression" and inner.type == "identifier":
                        lam = v.child_by_field_name("declarator")
                        params = lam.child_by_field_name("parameters") if lam is not None else None
                        self.lambdas[(self.cur, self.text(inner))] = frozenset(self._writable_params(params))
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
                params = inner.child_by_field_name("parameters")
                token = M.out_token(self._writable_params(params))
                if token:
                    fm.append(token)
                self.add_entity(kind, name, node, name_node, longname=self._longname(name, owner),
                                signature=sig[:300], modifiers=" ".join(fm), type=tname, owner=owner)
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
                if "extern" not in vm and self._holds_object(d):
                    self._object_ref(typ, inner, self.text(inner))
                if in_func:
                    self._note_local(node, d, typ, inner, init.child_by_field_name("value") if init is not None else None)
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
                init = ""                 # default value: 'int x = 3', 'ScalarT x{0}'
                if dv is not None:
                    init = self.compact(dv) if dv.type == "initializer_list" else " = " + self.compact(dv)
                lid = self.add_entity(M.FIELD, self.text(inner), d, inner, type=tname, modifiers=" ".join(mods),
                                      signature=(self.compact(typ) + " " + self.compact(d) + init)[:200]
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
            right = node.child_by_field_name("right")
            auto = t is not None and t.type == "placeholder_type_specifier"
            key = (self.cur, self.text(d))
            if not self._is_const(node) and self._is_lvalue_ref(node.child_by_field_name("declarator"), auto) \
                    and right is not None:
                # for (auto& x : b.items): x is an element of b.items
                alias = _Alias(False, auto, node.start_byte, node.end_byte)
                self._add_alias_targets(alias, [right], "[]")
                self.aliases.setdefault(key, []).append(alias)
            elif key in self.aliases:
                self.aliases[key].append(_Alias(False, auto, node.start_byte, node.end_byte))
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
                if name in MUTATORS:            # links.push_back(x) modifies links
                    self._mark_set(arg, call=name, arrow=self.text(fn.child_by_field_name("operator")) == "->")
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
        callee = self._callee(fn)
        if callee is not None:
            self._note_arguments(node, *callee)
            if callee[0] in FACTORIES:
                self._factory_object(fn)
        return None

    def _callee(self, fn):
        """(name, qualifier) of the function expression of a call, or None."""
        if fn.type == "identifier":
            return self.text(fn), ""
        if fn.type == "field_expression":
            f = fn.child_by_field_name("field")
            if f is None:
                return None
            name = self.text(f.child_by_field_name("name") if f.type == "template_method" else f)
            return name, self.compact(fn.child_by_field_name("argument"))
        if fn.type == "qualified_identifier":
            q, _, name = strip_templates(self.compact(fn)).replace(" ", "").rpartition("::")
            return name, q
        if fn.type == "template_function":
            return self.text(fn.child_by_field_name("name")), ""
        return None

    def _note_arguments(self, node, callee, qual):
        """Record the names passed to a call: the callee may change them through its parameters."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return
        items = [a for a in args.named_children if a.type != "comment"]
        writable = self.lambdas.get((self.cur, callee)) if not qual else None
        for i, a in enumerate(items):
            mode, found = "r", None
            if a.type == "pointer_expression" and self.text(a.child_by_field_name("operator")) == "&":
                a, mode = a.child_by_field_name("argument"), "p"
            elif a.type == "identifier":
                pointer = self._alias_of(self.text(a), a.start_byte)
                if pointer is not None and pointer.pointer:   # f(p) gives f the address p holds
                    mode, found = "p", (pointer, a, "part")
            if writable is not None:          # local lambda: its parameters are known right here
                if f"{mode}{i}" in writable and found is None:
                    self._mark_set(a)
                elif f"{mode}{i}" in writable:
                    self._write_through(found)
                continue
            for name, obj in self._write_chain(a):
                if name is not None:
                    for q in self._quals(self.compact(obj) if obj is not None else "", name.start_byte):
                        self.ref(M.OUT_ARG, self.text(name), name, q,
                                 detail=f"{i}\t{len(items)}\t{mode}\t\t{callee}\t{qual}")
            found = found or self._alias_root(a)
            if found is not None:
                local = self.text(found[1])
                for name, q in self._alias_names(found[0]):
                    if name is not None:
                        self.ref(M.OUT_ARG, name, a, q, detail=f"{i}\t{len(items)}\t{mode}\t{local}\t{callee}\t{qual}")

    def _factory_object(self, fn):
        """make_unique<Foo>(...): the first template argument is the created type."""
        stack = [fn]
        while stack:
            n = stack.pop()
            if n.type == "template_argument_list":
                td = next((c for c in n.named_children if c.type == "type_descriptor"), None)
                if td is not None:
                    self._object_ref(td.child_by_field_name("type"), fn)
                return
            stack.extend(n.named_children)

    def on_compound_literal_expression(self, node):
        self._object_ref(node.child_by_field_name("type"), node)
        return None

    def on_return_statement(self, node):
        if self.cur in self.ref_returns:
            expr = next((c for c in node.named_children if c.type != "comment"), None)
            for name, obj in self._write_chain(expr):
                if name is not None:
                    for q in self._quals(self.compact(obj) if obj is not None else "", name.start_byte):
                        self.ref(M.EXPOSE, self.text(name), name, q)
            found = self._alias_root(expr)
            if found is not None:
                for name, q in self._alias_names(found[0]):
                    if name is not None:
                        self.ref(M.EXPOSE, name, expr, q)
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
        text = self.compact(arg)
        for q in self._quals(text, f.start_byte) if kind == M.SET else [text]:
            self.ref(kind, self.text(f), f, q)
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

    def _write_chain(self, left):
        """Yield (name node, object expression) for each name modified by writing to *left*.

        In ``a.b[i].c = x`` the object ``a`` and its members ``b`` and ``c`` are all modified;
        the walk stops at ``->`` because writing through a pointer does not modify the pointer.
        A chain that starts at a call, as in ``f(x).c = y``, ends with (None, call node).
        """
        while left is not None:
            t = left.type
            if t == "identifier":
                yield left, None
                return
            if t == "field_expression":
                f = left.child_by_field_name("field")
                arg = left.child_by_field_name("argument")
                if f is not None:
                    yield f, arg
                op = left.child_by_field_name("operator")
                if op is not None and self.text(op) == "->":
                    return
                left = arg
            elif t == "subscript_expression":
                left = left.child_by_field_name("argument")
            elif t == "parenthesized_expression":
                left = left.named_children[0] if left.named_children else None
            else:
                if t == "call_expression":
                    yield None, left
                return

    def _mark_set(self, left, call="", arrow=False):
        """Mark the names written by an assignment target, or by the mutating member function *call*.

        *arrow*: the call is made through ``->``, so it changes what the pointer *left* points at.
        """
        for name, obj in self._write_chain(left):
            if name is not None:
                self.set_nodes.add(name.start_byte)
            else:
                self._write_call_ref(obj, obj)
        found = self._alias_root(left)
        if arrow and left is not None and left.type == "identifier":
            a = self._alias_of(self.text(left), left.start_byte)
            found = (a, left, "") if a is not None and a.pointer else None
        if found is not None:
            alias, local, how = found
            self._write_through((alias, local, how or call))

    def _write_through(self, found):
        """Record the writes made through an alias, at the position of the alias."""
        alias, local, how = found
        name_text = self.text(local)
        for name, q in self._alias_names(alias):
            if name is None:
                self._write_call_ref(q, local, name_text)
            else:
                self.ref(M.SET, name, local, q, detail=f"{M.ALIAS_PREFIX}{name_text}\t{how}")

    def on_assignment_expression(self, node):
        left = node.child_by_field_name("left")
        self._mark_set(left)
        if left is not None and left.type == "identifier":
            # p = &x: p may point at x from here on.  Older targets stay: a branch may not have run.
            a = self._alias_entry(self.text(left), left.start_byte)
            if a is not None and a.pointer:
                self._add_alias_targets(a, self._alias_targets(node.child_by_field_name("right"), True))

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
