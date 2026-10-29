"""Control flow graph construction for C/C++ and Python functions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .languages import get_parser

_WS = re.compile(r"\s+")

FUNC_TYPES = {"function_definition", "lambda_expression"}
BLOCKS = {"compound_statement", "block", "else_clause", "statement_list", "finally_clause"}
LOOPS = {"while_statement", "for_statement", "for_range_loop", "for_in_statement"}
TERMINATORS = {"return_statement", "throw_statement", "raise_statement"}
SKIP_TYPES = {"comment", "{", "}", ":", ";", "pass_statement", "preproc_call", "preproc_include",
              "pragma"}


@dataclass
class CFGNode:
    id: int
    kind: str          # start, end, block, cond, loop, switch, case, return, jump, try, handler
    label: str
    line: int = 0
    end_line: int = 0


@dataclass
class CFG:
    name: str
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)    # (src, dst, label)

    def add(self, kind, label, node=None):
        n = CFGNode(len(self.nodes), kind, label,
                    node.start_point[0] + 1 if node is not None else 0,
                    node.end_point[0] + 1 if node is not None else 0)
        self.nodes.append(n)
        return n.id

    def connect(self, preds, target):
        for p, lbl in preds:
            if (p, target, lbl) not in self.edges:
                self.edges.append((p, target, lbl))


class _Ctx:
    def __init__(self):
        self.breaks = []       # stack of lists of preds
        self.continues = []    # stack of loop head ids
        self.labels = {}
        self.gotos = []


class CFGBuilder:
    def __init__(self, src: bytes, lang: str):
        self.src = src
        self.lang = lang

    def text(self, n):
        return self.src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def short(self, n, limit=48):
        t = _WS.sub(" ", self.text(n)).strip()
        return t if len(t) <= limit else t[:limit - 1] + "…"

    def header(self, n, body):
        if body is None:
            return self.short(n)
        t = self.src[n.start_byte:body.start_byte].decode("utf-8", "replace")
        t = _WS.sub(" ", t).strip().rstrip(":").strip()
        return t if len(t) <= 48 else t[:47] + "…"

    def build(self, func_node, name) -> CFG:
        g = CFG(name)
        self.g = g
        start = g.add("start", name, func_node)
        self.end = g.add("end", "end")
        ctx = _Ctx()
        body = func_node.child_by_field_name("body")
        preds = [(start, "")]
        if body is not None:
            preds = self.stmt(body, preds, ctx)
        g.connect(preds, self.end)
        for gid, label in ctx.gotos:
            if label in ctx.labels:
                g.connect([(gid, "")], ctx.labels[label])
        g.nodes[self.end].line = func_node.end_point[0] + 1
        return g

    def children(self, n):
        return [c for c in n.named_children if c.type not in SKIP_TYPES]

    def seq(self, stmts, preds, ctx):
        pending = []     # consecutive simple statements merged into one block
        for s in stmts:
            if self.is_simple(s):
                pending.append(s)
                continue
            if pending:
                preds = self.block(pending, preds)
                pending = []
            preds = self.stmt(s, preds, ctx)
        if pending:
            preds = self.block(pending, preds)
        return preds

    COMPOUND = frozenset({
        "if_statement", "do_statement", "switch_statement", "try_statement", "break_statement",
        "continue_statement", "goto_statement", "labeled_statement", "match_statement",
        "with_statement", "case_statement",
    })

    def is_simple(self, n):
        t = n.type
        if t in BLOCKS or t in LOOPS or t in TERMINATORS or t in self.COMPOUND:
            return False
        return not self._has_throw(n)

    def _has_throw(self, n):
        return n.type == "expression_statement" and n.named_child_count == 1 and \
            n.named_children[0].type == "throw_expression"

    def block(self, stmts, preds):
        if not preds:
            return []
        lines = [self.short(s, 40) for s in stmts[:3]]
        if len(stmts) > 3:
            lines.append(f"… (+{len(stmts) - 3} more)")
        nid = self.g.add("block", "\n".join(lines), stmts[0])
        self.g.nodes[nid].end_line = stmts[-1].end_point[0] + 1
        self.g.connect(preds, nid)
        return [(nid, "")]

    def stmt(self, n, preds, ctx):
        g = self.g
        t = n.type
        if not preds and t not in ("labeled_statement", "case_statement"):
            return []
        if t in BLOCKS:
            return self.seq(self.children(n), preds, ctx)
        if t == "if_statement":
            return self._if(n, preds, ctx)
        if t in LOOPS:
            body = n.child_by_field_name("body")
            head = g.add("loop", self.header(n, body), n)
            g.connect(preds, head)
            ctx.breaks.append([])
            ctx.continues.append(head)
            ends = self.stmt(body, [(head, "T")], ctx) if body is not None else [(head, "T")]
            g.connect(ends, head)
            brk = ctx.breaks.pop()
            ctx.continues.pop()
            exits = [(head, "F")] + brk
            alt = n.child_by_field_name("alternative")       # python for/while ... else
            if alt is not None:
                exits = self.stmt(alt, [(head, "F")], ctx) + brk
            return exits
        if t == "do_statement":
            body = n.child_by_field_name("body")
            cond = n.child_by_field_name("condition")
            entry = g.add("block", "do", n)
            g.connect(preds, entry)
            head = g.add("loop", "while " + (self.short(cond) if cond is not None else ""), cond or n)
            ctx.breaks.append([])
            ctx.continues.append(head)
            ends = self.stmt(body, [(entry, "")], ctx) if body is not None else [(entry, "")]
            g.connect(ends, head)
            g.connect([(head, "T")], entry)
            brk = ctx.breaks.pop()
            ctx.continues.pop()
            return [(head, "F")] + brk
        if t in ("switch_statement", "match_statement"):
            return self._switch(n, preds, ctx)
        if t == "try_statement":
            return self._try(n, preds, ctx)
        if t == "with_statement":
            body = n.child_by_field_name("body")
            w = g.add("block", self.header(n, body), n)
            g.connect(preds, w)
            return self.stmt(body, [(w, "")], ctx) if body is not None else [(w, "")]
        if t in TERMINATORS or self._has_throw(n):
            r = g.add("return", self.short(n), n)
            g.connect(preds, r)
            g.connect([(r, "")], self.end)
            return []
        if t == "break_statement":
            j = g.add("jump", "break", n)
            g.connect(preds, j)
            if ctx.breaks:
                ctx.breaks[-1].append((j, ""))
            return []
        if t == "continue_statement":
            j = g.add("jump", "continue", n)
            g.connect(preds, j)
            if ctx.continues:
                g.connect([(j, "")], ctx.continues[-1])
            return []
        if t == "goto_statement":
            j = g.add("jump", self.short(n), n)
            g.connect(preds, j)
            lbl = n.child_by_field_name("label")
            if lbl is not None:
                ctx.gotos.append((j, self.text(lbl)))
            return []
        if t == "labeled_statement":
            lbl = n.child_by_field_name("label")
            nid = g.add("block", (self.text(lbl) if lbl is not None else "") + ":", n)
            g.connect(preds, nid)
            if lbl is not None:
                ctx.labels[self.text(lbl)] = nid
            rest = [c for c in self.children(n) if lbl is None or c.id != lbl.id]
            return self.seq(rest, [(nid, "")], ctx)
        return self.block([n], preds)

    def _if(self, n, preds, ctx):
        g = self.g
        cond = n.child_by_field_name("condition")
        cons = n.child_by_field_name("consequence")
        label = "if " + (self.short(cond) if cond is not None else "")
        c = g.add("cond", label, n)
        g.connect(preds, c)
        out = self.stmt(cons, [(c, "T")], ctx) if cons is not None else [(c, "T")]
        if self.lang == "python":
            false_preds = [(c, "F")]
            for alt in n.children_by_field_name("alternative"):
                if alt.type == "elif_clause":
                    ec = alt.child_by_field_name("condition")
                    e = g.add("cond", "elif " + (self.short(ec) if ec is not None else ""), alt)
                    g.connect(false_preds, e)
                    body = alt.child_by_field_name("consequence")
                    out += self.stmt(body, [(e, "T")], ctx) if body is not None else [(e, "T")]
                    false_preds = [(e, "F")]
                else:
                    body = alt.child_by_field_name("body")
                    out += self.stmt(body, false_preds, ctx) if body is not None else false_preds
                    false_preds = []
            return out + false_preds
        alt = n.child_by_field_name("alternative")
        if alt is None:
            return out + [(c, "F")]
        inner = self.children(alt)
        return out + self.seq(inner, [(c, "F")], ctx)

    def _switch(self, n, preds, ctx):
        g = self.g
        body = n.child_by_field_name("body")
        cond = n.child_by_field_name("condition") or n.child_by_field_name("subject")
        s = g.add("switch", ("switch " if self.lang != "python" else "match ") +
                  (self.short(cond) if cond is not None else ""), n)
        g.connect(preds, s)
        ctx.breaks.append([])
        fall = []
        has_default = False
        exits = []
        cases = self.children(body) if body is not None else []
        for case in cases:
            if case.type not in ("case_statement", "case_clause"):
                continue
            if self.lang == "python":
                pat = [c for c in case.named_children if c.type == "case_pattern"]
                label = "case " + ", ".join(self.short(p, 24) for p in pat)
                if any(self.text(p).strip() == "_" for p in pat):
                    has_default = True
                stmts = [c for c in case.named_children if c.type == "block"]
            else:
                val = case.child_by_field_name("value")
                if val is None:
                    has_default = True
                    label = "default"
                else:
                    label = "case " + self.short(val, 24)
                stmts = [c for c in self.children(case) if val is None or c.id != val.id]
            cn = g.add("case", label, case)
            g.connect([(s, "")] + fall, cn)
            ends = self.seq(stmts, [(cn, "")], ctx)
            if self.lang == "python":
                exits += ends
                fall = []
            else:
                fall = ends
        brk = ctx.breaks.pop()
        exits += fall + brk
        if not has_default:
            exits.append((s, "default"))
        return exits

    def _try(self, n, preds, ctx):
        g = self.g
        body = n.child_by_field_name("body")
        t = g.add("try", "try", n)
        g.connect(preds, t)
        out = self.stmt(body, [(t, "")], ctx) if body is not None else [(t, "")]
        fin = None
        else_ = None
        for c in n.named_children:
            if c.type in ("catch_clause", "except_clause"):
                h = g.add("handler", self.short(c, 40).split("{")[0].split(":")[0].strip() or "catch", c)
                g.connect([(t, "exception")], h)
                hb = c.child_by_field_name("body") or next(
                    (x for x in c.named_children if x.type in ("block", "compound_statement")), None)
                out += self.stmt(hb, [(h, "")], ctx) if hb is not None else [(h, "")]
            elif c.type == "finally_clause":
                fin = c
            elif c.type == "else_clause":
                else_ = c
        if else_ is not None:
            pass
        if fin is not None:
            f = g.add("block", "finally", fin)
            g.connect(out, f)
            blk = next((x for x in fin.named_children if x.type == "block"), None)
            out = self.stmt(blk, [(f, "")], ctx) if blk is not None else [(f, "")]
        return out


def find_function_node(tree, line, col=1):
    """Smallest function definition node starting at/around a 1-based position."""
    root = tree.root_node
    pt = (max(0, line - 1), max(0, col - 1))
    n = root.descendant_for_point_range(pt, pt)
    while n is not None and n.type != "function_definition":
        n = n.parent
    return n


def build_cfg(db, entity) -> CFG | None:
    f = db.entity_file(entity)
    if f is None:
        return None
    with open(f.path, "rb") as fh:
        src = fh.read().replace(b"\r\n", b"\n")
    if src.startswith(b"\xef\xbb\xbf"):
        src = src[3:]
    if f.lang in ("c", "cpp"):
        from .analyzer import scrub_macros
        src = scrub_macros(src, db.config().get("ignore_macros") or ())
    tree = get_parser(f.lang).parse(src)
    node = find_function_node(tree, entity.line, entity.col)
    if node is None:
        return None
    return CFGBuilder(src, f.lang).build(node, entity.name)
