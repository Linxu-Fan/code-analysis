"""Command line interface: codescope <command> ..."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys

from . import APP_NAME, __version__
from .core import model as M
from .core.languages import SELECTABLE, display_name

KIND_GROUPS = {
    "file": [M.FILE],
    "function": sorted(M.FUNCTION_KINDS),
    "class": sorted(M.CLASS_KINDS),
    "variable": [M.VARIABLE, M.FIELD],
    "macro": [M.MACRO],
    "namespace": [M.NAMESPACE, M.PACKAGE],
    "typedef": [M.TYPEDEF],
    "unresolved": sorted(M.UNRESOLVED_KINDS),
}


def _open(path):
    from .core.database import Database
    if not os.path.exists(path):
        sys.exit(f"error: project database not found: {path}")
    return Database(path)


def _progress(quiet):
    last = {"stage": None}

    def cb(stage, i, n, msg=""):
        if quiet:
            return
        if stage == "Parsing":
            sys.stderr.write(f"\r[{i}/{n}] {msg[-60:]:<60}")
            sys.stderr.flush()
            if i == n:
                sys.stderr.write("\n")
        elif stage != last["stage"]:
            sys.stderr.write(f"{stage}: {msg}\n")
        last["stage"] = stage
    return cb


def _find(db, name):
    ents = db.entities(name=name, include_locals=False) or [
        e for e in db.entities(like=f"%{name}%", include_locals=False) if e.longname == name]
    if not ents:
        ents = [e for e in db.entities(like=f"%{name}", include_locals=False)
                if e.longname.endswith(name) or e.name == name]
    if not ents:
        sys.exit(f"error: no entity named '{name}'")
    ents.sort(key=lambda e: (e.is_unresolved, e.kind == M.FILE, e.longname))
    return ents


def cmd_create(a):
    from .core.analyzer import create_project
    langs = a.lang or SELECTABLE
    for l in langs:
        if l not in SELECTABLE:
            sys.exit(f"error: unsupported language '{l}' (choose from {', '.join(SELECTABLE)})")
    db = create_project(a.db, a.name or os.path.splitext(os.path.basename(a.db))[0], a.root, langs, None)
    cfg = db.config()
    if a.exclude:
        cfg["exclude"] = cfg["exclude"] + a.exclude
    try:
        cfg["folders"] = _folder_selection(cfg, a.folder, a.own_code)
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    db.set_config(cfg)
    if cfg["folders"]:
        from .core.folders import describe
        print("Analyzing folders: " + describe(cfg["folders"], cfg["roots"]))
    print(f"Created {db.path}")
    if a.analyze:
        a.full = False
        a.quiet = False
        return cmd_analyze(a)


def _folder_selection(cfg, folders, own_code):
    """Folder selection from --folder (relative to a project folder or absolute) and --own-code."""
    from .core.folders import default_selection, norm, scan_tree
    sel = []
    for f in folders or []:
        cands = [f] if os.path.isabs(f) else [os.path.join(r, f) for r in cfg["roots"]]
        hit = next((c for c in cands if os.path.isdir(c)), None)
        if hit is None:
            raise ValueError(f"folder not found in the project: {f}")
        sel.append({"path": norm(hit), "recursive": True})
    if own_code:
        for r in cfg["roots"]:
            tree = scan_tree(r, cfg.get("languages"), cfg.get("exclude") or [])
            sel += [s for s in default_selection(tree)
                    if not folders or any(s["path"].startswith(x["path"]) for x in sel)]
    return sel


def cmd_add(a):
    db = _open(a.db)
    cfg = db.config()
    for r in a.root:
        p = os.path.abspath(r)
        if p not in cfg["roots"]:
            cfg["roots"].append(p)
    db.set_config(cfg)
    print("Source roots:\n  " + "\n  ".join(cfg["roots"]))


def cmd_remove(a):
    db = _open(a.db)
    cfg = db.config()
    rm = {os.path.abspath(r) for r in a.root}
    cfg["roots"] = [r for r in cfg["roots"] if r not in rm]
    db.set_config(cfg)
    print("Source roots:\n  " + "\n  ".join(cfg["roots"]))


def cmd_settings(a):
    db = _open(a.db)
    cfg = db.config()
    changed = False
    if a.languages:
        cfg["languages"] = a.languages
        changed = True
    if a.exclude:
        cfg["exclude"] = a.exclude
        changed = True
    if a.header_lang:
        cfg["header_lang"] = a.header_lang
        changed = True
    if a.folder is not None or a.all_folders or a.own_code:
        try:
            cfg["folders"] = [] if a.all_folders else _folder_selection(cfg, a.folder, a.own_code)
        except ValueError as exc:
            sys.exit(f"error: {exc}")
        changed = True
    if changed:
        db.set_config(cfg)
    print(json.dumps(cfg, indent=2))


def cmd_analyze(a):
    from .core.analyzer import Analyzer
    db = _open(a.db)
    st = Analyzer(db, _progress(a.quiet)).analyze(full=a.full)
    print(f"Analyzed {st['files']} files ({st['parsed']} parsed, {st['removed']} removed, "
          f"{st['errors']} errors) in {st['seconds']}s")


def cmd_list(a):
    db = _open(a.db)
    kinds = None
    if a.kind:
        kinds = []
        for k in a.kind:
            kinds += KIND_GROUPS.get(k.lower(), [k])
    for e in db.entities(kinds=kinds, include_locals=bool(a.locals)):
        if a.name and not (fnmatch.fnmatch(e.name, a.name) or fnmatch.fnmatch(e.longname, a.name)):
            continue
        print(f"{e.kind:<20} {e.longname:<50} {db.location(e)}")


def cmd_info(a):
    db = _open(a.db)
    for e in _find(db, a.name)[: a.max]:
        print(f"{e.longname}  [{e.kind}]")
        if e.signature:
            print(f"  signature : {e.signature}")
        if e.file_id:
            print(f"  defined   : {db.location(e)}")
        for d in db.declarations(e.id):
            print(f"  declared  : {db.location(d)}")
        if e.modifiers:
            print(f"  modifiers : {e.modifiers}")
        if e.type:
            print(f"  type      : {e.type}")
        m = db.metrics(e.id)
        if m:
            print("  metrics   : " + ", ".join(f"{k}={v:g}" for k, v in sorted(m.items())))
        for kind, (fwd, back) in M.REF_LABELS.items():
            outs = db.related(e.id, (kind,))
            ins = db.related(e.id, (kind,), reverse=True)
            if outs:
                print(f"  {fwd}: " + ", ".join(x.longname for x in outs[:30]) + (" ..." if len(outs) > 30 else ""))
            if ins:
                print(f"  {back}: " + ", ".join(x.longname for x in ins[:30]) + (" ..." if len(ins) > 30 else ""))
        print()


def cmd_metrics(a):
    from .core.report import metrics_csv
    db = _open(a.db)
    kinds = None
    if a.kind:
        kinds = []
        for k in a.kind:
            kinds += KIND_GROUPS.get(k.lower(), [k])
    if a.project:
        print(json.dumps(db.project_metrics(), indent=2))
        return
    text = metrics_csv(db, kinds, a.metric or None)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"Wrote {a.out}")
    else:
        sys.stdout.write(text)


def cmd_report(a):
    from .core.report import generate_html
    db = _open(a.db)
    out = a.out or os.path.splitext(db.path)[0] + "_report"
    index = generate_html(db, out, include_source=not a.no_source, progress=_progress(a.quiet))
    print(f"Report written to {index}")


def cmd_check(a):
    from .core.codecheck import RULES, run_codecheck
    if a.list_rules:
        for r in RULES:
            thr = f" (threshold {r.threshold:g})" if r.threshold is not None else ""
            print(f"{r.id:<9} {r.severity:<8} {r.title}{thr}  [{', '.join(display_name(l) for l in r.langs)}]")
        return 0
    if not a.db:
        sys.exit("error: project database required")
    db = _open(a.db)
    thresholds = {}
    for t in a.threshold or []:
        k, _, v = t.partition("=")
        thresholds[k] = float(v)
    enabled = None
    if a.rules:
        enabled = set()
        for pat in a.rules:
            enabled |= {r.id for r in RULES if fnmatch.fnmatch(r.id, pat)}
    vs = run_codecheck(db, enabled, thresholds)
    if a.format == "json":
        print(json.dumps([v.__dict__ for v in vs], indent=2))
    elif a.format == "csv":
        import csv
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["severity", "rule", "file", "line", "entity", "message"])
        for v in vs:
            w.writerow([v.severity, v.rule, v.path, v.line, v.entity, v.message])
    else:
        for v in vs:
            print(f"{v.path}:{v.line}: {v.severity}: [{v.rule}] {v.message}")
        counts = {s: sum(1 for v in vs if v.severity == s) for s in ("error", "warning", "info")}
        print(f"\n{len(vs)} violations: {counts['error']} errors, {counts['warning']} warnings, "
              f"{counts['info']} infos", file=sys.stderr)
    order = {"info": 0, "warning": 1, "error": 2}
    if a.fail_on and any(order[v.severity] >= order[a.fail_on] for v in vs):
        return 1
    return 0


def cmd_graph(a):
    from .core.graphs import architecture_graph, build_graph, graphs_for
    from .core.layout import layout
    from .core.svg import to_dot, to_svg
    db = _open(a.db)
    if a.type == "architecture":
        g = architecture_graph(db)
    else:
        e = _find(db, a.name)[0] if a.name else None
        if e is None:
            sys.exit("error: entity name required")
        kind = a.type or graphs_for(e)[0]
        g = build_graph(db, kind, e, depth=a.depth, hide_unresolved=a.hide_unresolved)
    if a.lr:
        g.direction = "LR"
    layout(g)
    out = to_dot(g) if (a.format == "dot" or (a.out or "").endswith(".dot")) else to_svg(g)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"Wrote {a.out}")
    else:
        print(out)


def cmd_calltree(a):
    from .core.calltree import build_call_tree, to_svg, to_text
    db = _open(a.db)
    funcs = [e for e in _find(db, a.name) if e.kind in M.FUNCTION_KINDS]
    if not funcs:
        sys.exit(f"error: '{a.name}' is not a function")
    e = funcs[0]
    down = a.down or not a.up
    tree = build_call_tree(db, e, descendants=down, ancestors=a.up, depth=a.depth,
                           hide_unresolved=a.hide_unresolved, expand_repeated=a.repeat)
    if a.out and a.out.lower().endswith(".svg"):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_svg(tree, f"Call tree: {e.longname}"))
        print(f"Wrote {a.out} ({tree.size} nodes)")
    elif a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_text(tree))
        print(f"Wrote {a.out} ({tree.size} nodes)")
    else:
        print(to_text(tree))


def cmd_classtree(a):
    from .core.calltree import build_class_tree, to_svg, to_text
    db = _open(a.db)
    classes = [e for e in _find(db, a.name) if e.kind in M.CLASS_KINDS]
    if not classes:
        sys.exit(f"error: '{a.name}' is not a class or struct")
    e = classes[0]
    both = not a.down and not a.up
    tree = build_class_tree(db, e, descendants=a.down or both, ancestors=a.up or both, depth=a.depth,
                            hide_unresolved=a.hide_unresolved, members=a.members, relation=a.relation)
    if a.out and a.out.lower().endswith(".svg"):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_svg(tree, f"Class tree: {e.longname}"))
        print(f"Wrote {a.out} ({tree.size} classes)")
    elif a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_text(tree))
        print(f"Wrote {a.out} ({tree.size} classes)")
    else:
        print(to_text(tree))


def cmd_modtree(a):
    from .core.calltree import build_modify_tree, modifiable_fields, to_svg, to_text
    db = _open(a.db)
    hits = [e for e in _find(db, a.name) if e.kind in M.CLASS_KINDS | {M.FIELD, M.PROPERTY}]
    if not hits:
        sys.exit(f"error: '{a.name}' is not a class, struct or member")
    e, member = hits[0], None
    if e.kind in (M.FIELD, M.PROPERTY):
        member, e = e, db.enclosing(e, M.CLASS_KINDS)
    if a.member:
        member = next((f for f in modifiable_fields(db, e) if f.name == a.member), None)
        if member is None:
            sys.exit(f"error: {e.longname} has no member '{a.member}'")
    tree = build_modify_tree(db, e, member, depth=a.depth, paths=not a.flat)
    title = f"Modification tree: {e.longname}" + (f".{member.name}" if member else "")
    if a.out and a.out.lower().endswith(".svg"):
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_svg(tree, title))
        print(f"Wrote {a.out} ({tree.writers} modifying functions)")
    elif a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(to_text(tree))
        print(f"Wrote {a.out} ({tree.writers} modifying functions)")
    else:
        print(to_text(tree))


def cmd_gui(a):
    from .gui.app import main as gui_main
    return gui_main([a.db] if a.db else [])


def build_parser():
    p = argparse.ArgumentParser(prog="codescope", description=f"{APP_NAME} {__version__} - static code analysis")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("create", help="create a new project database")
    s.add_argument("db")
    s.add_argument("-r", "--root", action="append", default=[], help="source directory (repeatable)")
    s.add_argument("-n", "--name")
    s.add_argument("-l", "--lang", nargs="+", help=f"languages: {' '.join(SELECTABLE)}")
    s.add_argument("-x", "--exclude", action="append", help="exclude glob, e.g. */third_party/*")
    s.add_argument("-f", "--folder", action="append",
                   help="only analyze this sub folder (repeatable, relative to the project folder), e.g. -f src -f include")
    s.add_argument("--own-code", action="store_true",
                   help="skip folders that look like third-party code or build output (third_party, external, ...)")
    s.add_argument("-a", "--analyze", action="store_true", help="analyze immediately")
    s.set_defaults(fn=cmd_create)

    s = sub.add_parser("add", help="add source directories")
    s.add_argument("db")
    s.add_argument("root", nargs="+")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("remove", help="remove source directories")
    s.add_argument("db")
    s.add_argument("root", nargs="+")
    s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("settings", help="show or change project settings")
    s.add_argument("db")
    s.add_argument("--languages", nargs="+")
    s.add_argument("--exclude", nargs="+")
    s.add_argument("--header-lang", choices=["auto", "c", "cpp"])
    s.add_argument("-f", "--folder", action="append", help="replace the folder selection (repeatable)")
    s.add_argument("--own-code", action="store_true", help="select own-code folders automatically")
    s.add_argument("--all-folders", action="store_true", help="analyze all folders again")
    s.set_defaults(fn=cmd_settings)

    s = sub.add_parser("analyze", help="analyze changed files (or everything with --full)")
    s.add_argument("db")
    s.add_argument("--full", action="store_true")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("list", help="list entities")
    s.add_argument("db")
    s.add_argument("-k", "--kind", nargs="+", help="file function class variable macro namespace typedef unresolved")
    s.add_argument("-n", "--name", help="glob on name or qualified name")
    s.add_argument("--locals", action="store_true", help="include parameters and local variables")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("info", help="show entity details and references")
    s.add_argument("db")
    s.add_argument("name")
    s.add_argument("--max", type=int, default=5)
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("metrics", help="export metrics as CSV")
    s.add_argument("db")
    s.add_argument("-k", "--kind", nargs="+")
    s.add_argument("-m", "--metric", nargs="+")
    s.add_argument("-o", "--out")
    s.add_argument("--project", action="store_true", help="project totals as JSON")
    s.set_defaults(fn=cmd_metrics)

    s = sub.add_parser("report", help="generate an HTML report")
    s.add_argument("db")
    s.add_argument("-o", "--out")
    s.add_argument("--no-source", action="store_true", help="do not include browsable source pages")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("check", help="run CodeCheck rules")
    s.add_argument("db", nargs="?")
    s.add_argument("--rules", nargs="+", help="rule ids or globs, e.g. DEAD-* CMPLX-01")
    s.add_argument("-t", "--threshold", action="append", help="RULE=value, e.g. CMPLX-01=10")
    s.add_argument("-f", "--format", choices=["text", "csv", "json"], default="text")
    s.add_argument("--fail-on", choices=["info", "warning", "error"], help="exit code 1 if violations found")
    s.add_argument("--list-rules", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("graph", help="export a graph as SVG or Graphviz DOT")
    s.add_argument("db")
    s.add_argument("name", nargs="?")
    s.add_argument("-t", "--type", choices=["calls", "callby", "butterfly", "inheritance", "uml", "includes",
                                            "includedby", "filedeps", "control", "architecture"])
    s.add_argument("-d", "--depth", type=int, default=3)
    s.add_argument("-f", "--format", choices=["svg", "dot"], default="svg")
    s.add_argument("-o", "--out")
    s.add_argument("--hide-unresolved", action="store_true")
    s.add_argument("--lr", action="store_true", help="left-to-right layout")
    s.set_defaults(fn=cmd_graph)

    s = sub.add_parser("calltree", help="callers (--up) and/or callees (--down) of a function as a tree")
    s.add_argument("db")
    s.add_argument("name", help="function name or qualified name")
    s.add_argument("--down", action="store_true", help="descendants: what the function calls (default)")
    s.add_argument("--up", action="store_true", help="ancestors: what calls the function")
    s.add_argument("-d", "--depth", type=int, default=4)
    s.add_argument("--hide-unresolved", action="store_true", help="hide calls into code outside the project")
    s.add_argument("--repeat", action="store_true", help="expand repeated subtrees every time")
    s.add_argument("-o", "--out", help="output file: .svg for a picture, anything else for indented text")
    s.set_defaults(fn=cmd_calltree)

    s = sub.add_parser("classtree", help="related classes of a class as a tree: bases/containers (--up), "
                       "members/derived classes (--down)")
    s.add_argument("db")
    s.add_argument("name", help="class or struct name or qualified name")
    s.add_argument("--down", action="store_true", help="descendants: member types and derived classes")
    s.add_argument("--up", action="store_true", help="ancestors: base classes and containing classes "
                   "(default: both directions)")
    s.add_argument("-r", "--relation", choices=("both", "inherit", "compose"), default="both",
                   help="inheritance, composition (data members) or both (default)")
    s.add_argument("-d", "--depth", type=int, default=6)
    s.add_argument("--members", action="store_true", help="list fields and methods (UML style, SVG output)")
    s.add_argument("--hide-unresolved", action="store_true", help="hide types defined outside the project")
    s.add_argument("-o", "--out", help="output file: .svg for a picture, anything else for indented text")
    s.set_defaults(fn=cmd_classtree)

    s = sub.add_parser("modtree", help="where and in which order a class/struct (or one member) is modified")
    s.add_argument("db")
    s.add_argument("name", help="class or struct, or a member such as Body::mass")
    s.add_argument("-m", "--member", help="only writes of this member")
    s.add_argument("--flat", action="store_true", help="list the modifying functions without call paths")
    s.add_argument("-d", "--depth", type=int, default=10)
    s.add_argument("-o", "--out", help="output file: .svg for a picture, anything else for indented text")
    s.set_defaults(fn=cmd_modtree)

    s = sub.add_parser("gui", help="open the graphical interface")
    s.add_argument("db", nargs="?")
    s.set_defaults(fn=cmd_gui)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    rc = a.fn(a)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
