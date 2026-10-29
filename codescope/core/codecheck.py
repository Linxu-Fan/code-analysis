"""CodeCheck: configurable coding-standard and quality rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import model as M
from .languages import get_parser

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    category: str
    langs: tuple = ("c", "cpp", "python")
    threshold: float | None = None
    pattern: str | None = None
    description: str = ""


@dataclass
class Violation:
    rule: str
    severity: str
    message: str
    file_id: int | None
    path: str
    line: int
    entity_id: int | None = None
    entity: str = ""


RULES = [
    # complexity / size
    Rule("CMPLX-01", "Function cyclomatic complexity too high", WARNING, "Complexity", threshold=15,
         description="McCabe complexity above the threshold makes functions hard to test."),
    Rule("CMPLX-02", "Function nesting too deep", WARNING, "Complexity", threshold=4),
    Rule("CMPLX-03", "Function too long", WARNING, "Size", threshold=100,
         description="Code lines in a single function."),
    Rule("CMPLX-04", "Too many parameters", WARNING, "Size", threshold=6),
    Rule("CMPLX-05", "File too long", INFO, "Size", threshold=2000),
    Rule("CMPLX-06", "Low maintainability index", WARNING, "Complexity", threshold=20),
    Rule("OO-01", "Class has too many methods", INFO, "Object Orientation", threshold=40),
    Rule("OO-02", "Inheritance tree too deep", WARNING, "Object Orientation", threshold=5),
    Rule("OO-03", "Class coupling too high", INFO, "Object Orientation", threshold=20),
    Rule("OO-04", "Polymorphic class without virtual destructor", WARNING, "Object Orientation", ("cpp",)),
    # dead code
    Rule("DEAD-01", "Function is never called", INFO, "Unused Code"),
    Rule("DEAD-02", "Global variable is never used", INFO, "Unused Code"),
    Rule("DEAD-03", "Parameter is never used", INFO, "Unused Code"),
    Rule("DEAD-04", "Local variable is never used", WARNING, "Unused Code"),
    Rule("DEAD-05", "Unresolved include / import", INFO, "Unused Code"),
    # naming
    Rule("NAME-01", "Class name should be CamelCase", INFO, "Naming", pattern=r"^_?[A-Z][A-Za-z0-9]*$"),
    Rule("NAME-02", "Python function name should be snake_case", INFO, "Naming", ("python",),
         pattern=r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$"),
    Rule("NAME-03", "Macro name should be UPPER_CASE", INFO, "Naming", ("c", "cpp"),
         pattern=r"^[A-Z][A-Z0-9_]*$"),
    # statements / constructs
    Rule("STMT-01", "Use of goto", WARNING, "Statements", ("c", "cpp")),
    Rule("STMT-02", "Empty exception handler", WARNING, "Statements"),
    Rule("STMT-03", "Bare except clause", WARNING, "Statements", ("python",)),
    Rule("STMT-04", "switch without default", INFO, "Statements", ("c", "cpp")),
    Rule("STMT-05", "Assignment used as condition", WARNING, "Statements", ("c", "cpp")),
    Rule("STMT-06", "Mutable default argument", WARNING, "Statements", ("python",)),
    Rule("STMT-07", "Comparison with None using == / !=", INFO, "Statements", ("python",)),
    Rule("STMT-08", "Wildcard import", INFO, "Statements", ("python",)),
    Rule("STMT-09", "'using namespace' in header", WARNING, "Statements", ("cpp",)),
    # style
    Rule("STYLE-01", "Line too long", INFO, "Style", threshold=120),
    Rule("STYLE-02", "TODO / FIXME comment", INFO, "Style"),
    Rule("STYLE-03", "Undocumented public function", INFO, "Documentation", threshold=15,
         description="Functions longer than the threshold without a leading comment or docstring."),
]
RULES_BY_ID = {r.id: r for r in RULES}

DEFAULT_ENABLED = {r.id for r in RULES} - {"STYLE-01", "STYLE-03", "NAME-02"}


def rule_settings(cfg: dict):
    cc = cfg.get("codecheck") or {}
    enabled = set(cc.get("enabled") or DEFAULT_ENABLED)
    thresholds = {r.id: r.threshold for r in RULES if r.threshold is not None}
    thresholds.update({k: float(v) for k, v in (cc.get("thresholds") or {}).items()})
    return enabled, thresholds


class CodeChecker:
    def __init__(self, db, enabled=None, thresholds=None):
        self.db = db
        cfg_enabled, cfg_thr = rule_settings(db.config())
        self.enabled = set(enabled) if enabled is not None else cfg_enabled
        self.thr = cfg_thr
        if thresholds:
            self.thr.update(thresholds)
        self.out: list[Violation] = []
        self._paths = {f.id: f.relpath for f in db.files()}
        self._langs = {f.id: f.lang for f in db.files()}

    def on(self, rid, lang=None):
        if rid not in self.enabled:
            return False
        return lang is None or lang in RULES_BY_ID[rid].langs

    def add(self, rid, msg, file_id, line, ent=None):
        r = RULES_BY_ID[rid]
        self.out.append(Violation(rid, r.severity, msg, file_id, self._paths.get(file_id, ""), line,
                                  ent.id if ent is not None else None, ent.longname if ent is not None else ""))

    # -----------------------------------------------------------------------------
    def run(self, progress=None) -> list[Violation]:
        self._metric_rules()
        self._unused_rules()
        self._naming_rules()
        files = self.db.files()
        for i, f in enumerate(files):
            if progress:
                progress("CodeCheck", i, len(files), f.relpath)
            try:
                with open(f.path, "rb") as fh:
                    src = fh.read().replace(b"\r\n", b"\n")
            except OSError:
                continue
            self._text_rules(f, src)
            self._ast_rules(f, src)
        sev = {ERROR: 0, WARNING: 1, INFO: 2}
        self.out.sort(key=lambda v: (v.path, v.line, sev.get(v.severity, 3), v.rule))
        return self.out

    def _metric_rules(self):
        db = self.db
        t = self.thr
        funcs, vals = db.metric_table(sorted(M.FUNCTION_KINDS))
        for e in funcs:
            m = vals.get(e.id, {})
            lang = e.lang
            if self.on("CMPLX-01") and m.get("Cyclomatic", 0) > t["CMPLX-01"]:
                self.add("CMPLX-01", f"{e.name}: cyclomatic complexity {int(m['Cyclomatic'])} > {t['CMPLX-01']:g}",
                         e.file_id, e.line, e)
            if self.on("CMPLX-02") and m.get("MaxNesting", 0) > t["CMPLX-02"]:
                self.add("CMPLX-02", f"{e.name}: nesting depth {int(m['MaxNesting'])} > {t['CMPLX-02']:g}",
                         e.file_id, e.line, e)
            if self.on("CMPLX-03") and m.get("CountLineCode", 0) > t["CMPLX-03"]:
                self.add("CMPLX-03", f"{e.name}: {int(m['CountLineCode'])} code lines > {t['CMPLX-03']:g}",
                         e.file_id, e.line, e)
            if self.on("CMPLX-04") and m.get("CountParams", 0) > t["CMPLX-04"]:
                self.add("CMPLX-04", f"{e.name}: {int(m['CountParams'])} parameters > {t['CMPLX-04']:g}",
                         e.file_id, e.line, e)
            if self.on("CMPLX-06") and "MaintainabilityIndex" in m and m["MaintainabilityIndex"] < t["CMPLX-06"]:
                self.add("CMPLX-06", f"{e.name}: maintainability index {m['MaintainabilityIndex']:g} "
                                     f"< {t['CMPLX-06']:g}", e.file_id, e.line, e)
            if self.on("STYLE-03", lang) and m.get("CountLineCode", 0) > t["STYLE-03"]:
                if "private" not in e.modifiers.split() and not e.name.startswith("_"):
                    self._doc_check(e)
        files, fvals = db.metric_table([M.FILE])
        for e in files:
            m = fvals.get(e.id, {})
            if self.on("CMPLX-05") and m.get("CountLine", 0) > t["CMPLX-05"]:
                self.add("CMPLX-05", f"{e.name}: {int(m['CountLine'])} lines > {t['CMPLX-05']:g}", e.file_id, 1, e)
        classes, cvals = db.metric_table(sorted(M.CLASS_KINDS))
        for e in classes:
            m = cvals.get(e.id, {})
            if self.on("OO-01") and m.get("CountDeclMethod", 0) > t["OO-01"]:
                self.add("OO-01", f"{e.name}: {int(m['CountDeclMethod'])} methods > {t['OO-01']:g}", e.file_id, e.line, e)
            if self.on("OO-02") and m.get("MaxInheritanceTree", 0) > t["OO-02"]:
                self.add("OO-02", f"{e.name}: inheritance depth {int(m['MaxInheritanceTree'])} > {t['OO-02']:g}",
                         e.file_id, e.line, e)
            if self.on("OO-03") and m.get("CountClassCoupled", 0) > t["OO-03"]:
                self.add("OO-03", f"{e.name}: coupled to {int(m['CountClassCoupled'])} classes > {t['OO-03']:g}",
                         e.file_id, e.line, e)
            if self.on("OO-04", e.lang):
                methods = db.children(e.id, sorted(M.FUNCTION_KINDS))
                virtual = [x for x in methods if {"virtual", "override", "pure"} & set(x.modifiers.split())]
                dtor = next((x for x in methods if x.name.startswith("~")), None)
                if virtual and dtor is None and not db.related(e.id, (M.INHERIT,)):
                    self.add("OO-04", f"{e.name} has virtual methods but no virtual destructor", e.file_id, e.line, e)
                elif virtual and dtor is not None and "virtual" not in dtor.modifiers.split() \
                        and "override" not in dtor.modifiers.split() and not db.related(e.id, (M.INHERIT,)):
                    self.add("OO-04", f"{e.name}: destructor is not virtual", e.file_id, dtor.line, e)

    def _doc_check(self, e):
        f = self.db.file(e.file_id)
        if f is None:
            return
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().split("\n")
        except OSError:
            return
        start = int(self.db.metric(e.id, "_start", e.line) or e.line)
        prev = lines[start - 2].strip() if start >= 2 else ""
        prev2 = lines[start - 3].strip() if start >= 3 else ""
        documented = prev.startswith(("//", "/*", "*", "#", "@")) or prev.endswith("*/") or prev2.endswith("*/")
        if e.lang == "python":
            for ln in lines[e.line:e.line + 6]:
                s = ln.strip()
                if s:
                    documented = documented or s.startswith(('"""', "'''", 'r"""', '"', "'"))
                    break
        if not documented:
            self.add("STYLE-03", f"{e.name} has no documentation comment", e.file_id, e.line, e)

    def _unused_rules(self):
        db = self.db
        q = db.conn.execute
        used = {r[0] for r in q("SELECT DISTINCT dst_id FROM refs WHERE dst_id IS NOT NULL")}
        if self.on("DEAD-01"):
            for e in db.entities(kinds=sorted(M.FUNCTION_KINDS)):
                if e.id in used or e.file_id is None:
                    continue
                mods = set(e.modifiers.split())
                if e.name in ("main", "WinMain", "wmain", "DllMain") or e.kind == M.CONSTRUCTOR:
                    continue
                if mods & {"virtual", "override", "pure", "destructor", "decl", "export"} or e.name.startswith(("~", "operator")):
                    continue
                if e.lang == "python" and (e.name.startswith("__") or e.name.startswith("test") or mods):
                    continue
                if e.kind == M.METHOD and self._overrides(e):
                    continue
                if e.lang == "python" and e.kind == M.FUNCTION and not e.name.startswith("_"):
                    # public module functions may be used by importers outside the project
                    parent = db.parent(e)
                    if parent is not None and parent.kind == M.FILE and self._is_init(parent):
                        continue
                self.add("DEAD-01", f"{e.name} is never called", e.file_id, e.line, e)
        if self.on("DEAD-02"):
            for e in db.entities(kinds=[M.VARIABLE]):
                if e.id in used or e.file_id is None or "decl" in e.modifiers.split():
                    continue
                if e.lang == "python" and (e.name.isupper() or e.name.startswith("__")):
                    continue
                self.add("DEAD-02", f"{e.name} is never used", e.file_id, e.line, e)
        if self.on("DEAD-03") or self.on("DEAD-04"):
            for e in db.entities(kinds=[M.PARAMETER, M.LOCAL]):
                if e.id in used or e.name.startswith("_") or e.name in ("self", "cls", "args", "kwargs", "argc", "argv"):
                    continue
                parent = db.parent(e)
                if parent is None or parent.kind not in M.FUNCTION_KINDS:
                    continue
                if e.kind == M.PARAMETER and self.on("DEAD-03"):
                    mods = set(parent.modifiers.split())
                    if mods & {"virtual", "override", "pure", "abstract"} or self._overrides(parent):
                        continue
                    if self.db.metric(parent.id, "CountStmt", 1) == 0:
                        continue
                    self.add("DEAD-03", f"parameter '{e.name}' of {parent.name} is never used", e.file_id, e.line, e)
                elif e.kind == M.LOCAL and self.on("DEAD-04"):
                    if "lambda" in e.modifiers.split():
                        continue
                    self.add("DEAD-04", f"local '{e.name}' in {parent.name} is never used", e.file_id, e.line, e)
        if self.on("DEAD-05"):
            for e in db.entities(kinds=[M.UNRESOLVED_INCLUDE, M.UNRESOLVED_MODULE]):
                refs = db.refs_to(e.id)
                for r in refs:
                    if r.qualifier == "system" or e.kind == M.UNRESOLVED_MODULE:
                        continue
                    self.add("DEAD-05", f"include \"{e.name}\" not found in project", r.file_id, r.line)

    def _is_init(self, file_ent):
        return file_ent.name == "__init__.py"

    def _overrides(self, m):
        cls = self.db.parent(m)
        if cls is None or cls.kind not in M.CLASS_KINDS:
            return False
        stack = list(self.db.related(cls.id, (M.INHERIT, M.IMPLEMENT)))
        seen = set()
        while stack:
            b = stack.pop()
            if b.id in seen:
                continue
            seen.add(b.id)
            if b.is_unresolved:
                return True      # unknown base class: assume it may declare the method
            if any(c.name == m.name for c in self.db.children(b.id, sorted(M.FUNCTION_KINDS))):
                return True
            stack.extend(self.db.related(b.id, (M.INHERIT, M.IMPLEMENT)))
        return False

    def _naming_rules(self):
        db = self.db
        checks = [("NAME-01", sorted(M.CLASS_KINDS)), ("NAME-02", [M.FUNCTION, M.METHOD]), ("NAME-03", [M.MACRO])]
        for rid, kinds in checks:
            if rid not in self.enabled:
                continue
            pat = re.compile(RULES_BY_ID[rid].pattern)
            for e in db.entities(kinds=kinds):
                if e.file_id is None or not self.on(rid, e.lang) or e.name.startswith("<"):
                    continue
                if rid == "NAME-03" and e.name.endswith(("_H", "_H_", "_HPP")):
                    continue
                if not pat.match(e.name):
                    self.add(rid, f"'{e.name}' does not match naming convention {RULES_BY_ID[rid].pattern}",
                             e.file_id, e.line, e)

    _TODO = re.compile(r"(//|#|/\*|\*)\s*.*\b(TODO|FIXME|XXX|HACK)\b")

    def _text_rules(self, f, src):
        long_on = self.on("STYLE-01")
        todo_on = self.on("STYLE-02")
        if not (long_on or todo_on):
            return
        limit = self.thr.get("STYLE-01", 120)
        for i, raw in enumerate(src.decode("utf-8", "replace").split("\n"), 1):
            if long_on and len(raw.rstrip()) > limit:
                self.add("STYLE-01", f"line has {len(raw.rstrip())} characters (> {limit:g})", f.id, i)
            if todo_on:
                m = self._TODO.search(raw)
                if m:
                    self.add("STYLE-02", raw.strip()[:100], f.id, i)

    def _ast_rules(self, f, src):
        lang = f.lang
        wanted = [r for r in ("STMT-01", "STMT-02", "STMT-03", "STMT-04", "STMT-05", "STMT-06", "STMT-07",
                              "STMT-08", "STMT-09") if self.on(r, lang)]
        if not wanted:
            return
        if lang in ("c", "cpp"):
            from .analyzer import scrub_macros
            src = scrub_macros(src, self.db.config().get("ignore_macros") or ())
        tree = get_parser(lang).parse(src)
        is_header = f.relpath.lower().endswith((".h", ".hpp", ".hh", ".hxx"))

        def text(n):
            return src[n.start_byte:n.end_byte].decode("utf-8", "replace")

        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            t = n.type
            line = n.start_point[0] + 1
            if t == "goto_statement" and "STMT-01" in wanted:
                self.add("STMT-01", "goto statement", f.id, line)
            elif t == "catch_clause" and "STMT-02" in wanted:
                body = n.child_by_field_name("body")
                if body is not None and not [c for c in body.named_children if c.type != "comment"]:
                    self.add("STMT-02", "empty catch block", f.id, line)
            elif t == "except_clause":
                if "STMT-03" in wanted and not any(c.type not in ("block", "comment") and c.is_named
                                                   for c in n.children):
                    self.add("STMT-03", "bare 'except:' catches SystemExit and KeyboardInterrupt", f.id, line)
                body = next((c for c in n.named_children if c.type == "block"), None)
                if "STMT-02" in wanted and body is not None and all(
                        c.type in ("pass_statement", "comment") for c in body.named_children):
                    self.add("STMT-02", "exception silently ignored", f.id, line)
            elif t == "switch_statement" and "STMT-04" in wanted:
                body = n.child_by_field_name("body")
                cases = [c for c in (body.named_children if body is not None else []) if c.type == "case_statement"]
                if cases and not any(c.child_by_field_name("value") is None for c in cases):
                    self.add("STMT-04", "switch statement has no default case", f.id, line)
            elif t in ("if_statement", "while_statement") and "STMT-05" in wanted:
                cond = n.child_by_field_name("condition")
                inner = cond
                while inner is not None and inner.type in ("condition_clause", "parenthesized_expression") \
                        and inner.named_child_count == 1:
                    inner = inner.named_children[0]
                if inner is not None and inner.type == "assignment_expression":
                    self.add("STMT-05", f"assignment in condition: {text(inner)[:60]}", f.id, line)
            elif t in ("default_parameter", "typed_default_parameter") and "STMT-06" in wanted:
                v = n.child_by_field_name("value")
                if v is not None and v.type in ("list", "dictionary", "set", "list_comprehension",
                                                 "dictionary_comprehension"):
                    nm = n.child_by_field_name("name")
                    self.add("STMT-06", f"mutable default for parameter '{text(nm)}'", f.id, line)
            elif t == "comparison_operator" and "STMT-07" in wanted:
                ops = [c.type for c in n.children if not c.is_named]
                if ("==" in ops or "!=" in ops) and any(c.type == "none" for c in n.named_children):
                    self.add("STMT-07", f"use 'is None' instead: {text(n)[:60]}", f.id, line)
            elif t == "wildcard_import" and "STMT-08" in wanted:
                self.add("STMT-08", "wildcard import pollutes the namespace", f.id, line)
            elif t == "using_declaration" and "STMT-09" in wanted and is_header:
                if text(n).split()[1:2] == ["namespace"]:
                    self.add("STMT-09", text(n).strip(), f.id, line)
            stack.extend(n.children)


def run_codecheck(db, enabled=None, thresholds=None, progress=None):
    return CodeChecker(db, enabled, thresholds).run(progress)
