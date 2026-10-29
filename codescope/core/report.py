"""HTML report and CSV export generation."""

from __future__ import annotations

import csv
import html
import io
import os
import time

from .. import __version__
from . import model as M
from .codecheck import RULES_BY_ID, run_codecheck
from .graphs import architecture_graph, inheritance_graph, uml_graph
from .languages import LANGUAGES, display_name
from .metrics import METRIC_DESCRIPTIONS
from .svg import to_svg

CSS = """
:root{--bg:#f7f8fa;--fg:#1f2328;--muted:#656d76;--card:#fff;--line:#d8dee4;--accent:#0b6bcb;--warn:#b35900;--err:#c62828}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 "Segoe UI",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--fg)}
header{background:#1d2633;color:#fff;padding:14px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0;font-weight:600}header nav a{color:#c9d6e3;margin-right:16px;text-decoration:none}
header nav a:hover{color:#fff}main{padding:24px;max-width:1400px;margin:auto}
h2{font-size:17px;margin:28px 0 10px}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:22px;font-weight:600}.card span{color:var(--muted);font-size:12px}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);font-size:13px}
th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#eef1f4;position:sticky;top:0;cursor:pointer;white-space:nowrap;user-select:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}tr:hover td{background:#f3f7fc}
.sev-error{color:var(--err);font-weight:600}.sev-warning{color:var(--warn);font-weight:600}.sev-info{color:var(--muted)}
.kind{color:var(--muted);font-size:12px}.xref{margin:0 0 18px;padding:10px 14px;background:var(--card);border:1px solid var(--line);border-radius:6px}
.xref h3{margin:0 0 4px;font-size:14px}.xref ul{margin:2px 0 6px 18px;padding:0}.graph{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:6px;padding:8px}
.src{font:12px/1.45 Consolas,Menlo,monospace;background:#fff;border:1px solid var(--line);overflow:auto}
.src table{border:0}.src td{border:0;padding:0 8px}.src .ln{color:#8c959f;text-align:right;user-select:none;width:1%}
.src tr:target td{background:#fff3c4}.bar{height:8px;background:#e6ebf1;border-radius:4px;overflow:hidden;min-width:80px}
.bar i{display:block;height:100%;background:var(--accent)}input.filter{padding:6px 10px;border:1px solid var(--line);border-radius:6px;width:320px;margin-bottom:8px}
footer{color:var(--muted);font-size:12px;padding:24px;text-align:center}
"""

JS = """
document.querySelectorAll('table.sortable').forEach(function(t){
  t.querySelectorAll('th').forEach(function(th,i){th.addEventListener('click',function(){
    var body=t.tBodies[0],rows=[].slice.call(body.rows),asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
    rows.sort(function(a,b){var x=a.cells[i].dataset.v||a.cells[i].textContent,y=b.cells[i].dataset.v||b.cells[i].textContent;
      var nx=parseFloat(x),ny=parseFloat(y);var r=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?r:-r;});
    rows.forEach(function(r){body.appendChild(r);});});});});
document.querySelectorAll('input.filter').forEach(function(inp){inp.addEventListener('input',function(){
  var q=inp.value.toLowerCase(),t=document.getElementById(inp.dataset.table);
  [].forEach.call(t.tBodies[0].rows,function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)>=0?'':'none';});});});
"""

FUNC_METRICS = ["CountLineCode", "Cyclomatic", "MaxNesting", "CountParams", "CountStmt", "CountInput",
                "CountOutput", "HalsteadVolume", "MaintainabilityIndex"]
CLASS_METRICS = ["CountLineCode", "CountDeclMethod", "CountDeclInstanceVariable", "SumCyclomatic",
                 "MaxInheritanceTree", "CountClassDerived", "CountClassCoupled", "PercentLackOfCohesion"]
FILE_METRICS = ["CountLine", "CountLineCode", "CountLineComment", "CountLineBlank", "RatioCommentToCode",
                "CountDeclFunction", "CountDeclClass", "SumCyclomatic", "MaxCyclomatic"]

esc = html.escape


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return f"{v:g}" if isinstance(v, float) else str(v)


class HtmlReport:
    def __init__(self, db, outdir, include_source=True, progress=None):
        self.db = db
        self.out = outdir
        self.include_source = include_source
        self.progress = progress or (lambda *a: None)
        self.name = db.config().get("name", "Project")

    def page(self, fname, title, body):
        nav = "".join(f'<a href="{f}">{t}</a>' for f, t in [
            ("index.html", "Summary"), ("dictionary.html", "Entities"), ("metrics.html", "Metrics"),
            ("codecheck.html", "CodeCheck"), ("graphs.html", "Graphs"), ("files.html", "Files")])
        doc = (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
               f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{esc(title)} - {esc(self.name)}</title>"
               f"<link rel=stylesheet href='style.css'></head><body><header><h1>{esc(self.name)}</h1><nav>{nav}</nav></header>"
               f"<main>{body}</main><footer>Generated by CodeScope {__version__} on {time.strftime('%Y-%m-%d %H:%M')}"
               f"</footer><script src='report.js'></script></body></html>")
        path = os.path.join(self.out, fname)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)

    def eref(self, e, label=None):
        label = esc(label or e.longname)
        if e.is_unresolved or e.file_id is None:
            return f"<span title='{esc(e.kind)}'>{label}</span>"
        return f"<a href='xref.html#e{e.id}'>{label}</a>"

    def srcref(self, file_id, line, label=None):
        f = self.db.file(file_id)
        if f is None:
            return ""
        label = esc(label or f"{f.relpath}:{line}")
        if self.include_source:
            return f"<a href='src/f{file_id}.html#L{line}'>{label}</a>"
        return label

    # ------------------------------------------------------------------------------------
    def generate(self):
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, "style.css"), "w", encoding="utf-8") as fh:
            fh.write(CSS)
        with open(os.path.join(self.out, "report.js"), "w", encoding="utf-8") as fh:
            fh.write(JS)
        steps = [self.summary, self.dictionary, self.xref, self.metrics, self.codecheck, self.graphs, self.files_page]
        if self.include_source:
            steps.append(self.sources)
        for i, step in enumerate(steps):
            self.progress("Report", i, len(steps), step.__name__)
            step()
        return os.path.join(self.out, "index.html")

    def summary(self):
        db = self.db
        pm = db.project_metrics()
        cards = [("Files", pm["Files"]), ("Lines", pm["CountLine"]), ("Code lines", pm["CountLineCode"]),
                 ("Comment lines", pm["CountLineComment"]), ("Comment/Code", pm["RatioCommentToCode"]),
                 ("Classes", pm["Classes"]), ("Functions", pm["Functions"]), ("Variables", pm["Variables"]),
                 ("Max cyclomatic", pm["MaxCyclomatic"]), ("Avg cyclomatic", pm["AvgCyclomatic"]),
                 ("References", pm["References"])]
        body = "<h2>Project summary</h2><div class=cards>" + "".join(
            f"<div class=card><b>{_fmt(v)}</b><span>{esc(k)}</span></div>" for k, v in cards) + "</div>"
        body += "<h2>Languages</h2><table><tr><th>Language</th><th class=n>Files</th><th class=n>Code lines</th></tr>"
        for lang, n in sorted(pm["Languages"].items()):
            loc = db.conn.execute("SELECT COALESCE(SUM(m.value),0) FROM metrics m JOIN entities e ON e.id=m.entity_id "
                                  "JOIN files f ON f.id=e.file_id WHERE e.kind='File' AND m.name='CountLineCode' "
                                  "AND f.lang=?", (lang,)).fetchone()[0]
            body += f"<tr><td>{esc(display_name(lang))}</td><td class=n>{n}</td><td class=n>{int(loc)}</td></tr>"
        body += "</table>"
        funcs, vals = db.metric_table(sorted(M.FUNCTION_KINDS))
        top = sorted(funcs, key=lambda e: -vals.get(e.id, {}).get("Cyclomatic", 0))[:15]
        body += "<h2>Most complex functions</h2><table><tr><th>Function</th><th>Location</th><th class=n>Cyclomatic</th>" \
                "<th class=n>Code lines</th><th class=n>Max nesting</th></tr>"
        for e in top:
            m = vals.get(e.id, {})
            body += (f"<tr><td>{self.eref(e)}</td><td>{self.srcref(e.file_id, e.line)}</td>"
                     f"<td class=n>{_fmt(m.get('Cyclomatic'))}</td><td class=n>{_fmt(m.get('CountLineCode'))}</td>"
                     f"<td class=n>{_fmt(m.get('MaxNesting'))}</td></tr>")
        body += "</table>"
        cfg = db.config()
        body += "<h2>Project configuration</h2><table>"
        body += f"<tr><th>Database</th><td>{esc(db.path)}</td></tr>"
        body += f"<tr><th>Source roots</th><td>{'<br>'.join(esc(r) for r in cfg.get('roots', []))}</td></tr>"
        body += f"<tr><th>Languages</th><td>{esc(', '.join(display_name(l) for l in cfg.get('languages', [])))}</td></tr>"
        body += f"<tr><th>Analyzed</th><td>{esc(db.get_meta('analyzed_at', '-'))}</td></tr></table>"
        self.page("index.html", "Summary", body)

    def _entities(self):
        return [e for e in self.db.entities(include_locals=False) if e.kind != M.FILE]

    def dictionary(self):
        ents = self._entities()
        rows = []
        for e in ents:
            loc = self.srcref(e.file_id, e.line) if e.file_id else "<span class=kind>external</span>"
            rows.append(f"<tr><td>{self.eref(e, e.name)}</td><td class=kind>{esc(e.kind)}</td>"
                        f"<td>{esc(e.longname)}</td><td>{loc}</td></tr>")
        body = ("<h2>Data dictionary</h2><input class=filter data-table=dict placeholder='Filter entities…'>"
                "<table id=dict class=sortable><thead><tr><th>Name</th><th>Kind</th><th>Qualified name</th>"
                f"<th>Location</th></tr></thead><tbody>{''.join(rows)}</tbody></table>")
        self.page("dictionary.html", "Entities", body)

    def xref(self, limit=60):
        db = self.db
        parts = ["<h2>Cross reference</h2>"]
        for e in self._entities():
            if e.file_id is None:
                continue
            s = [f"<div class=xref id='e{e.id}'><h3>{esc(e.longname)} <span class=kind>{esc(e.kind)}</span></h3>"]
            if e.signature:
                s.append(f"<code>{esc(e.signature)}</code><br>")
            s.append(f"Defined in {self.srcref(e.file_id, e.line)}")
            for d in db.declarations(e.id):
                s.append(f" &middot; declared in {self.srcref(d.file_id, d.line)}")
            groups = {}
            for r in db.refs_from(e.id):
                groups.setdefault(M.REF_LABELS.get(r.kind, (r.kind, r.kind))[0], []).append((r, r.dst_id))
            for r in db.refs_to(e.id):
                groups.setdefault(M.REF_LABELS.get(r.kind, (r.kind, r.kind))[1], []).append((r, r.src_id))
            for label, refs in groups.items():
                items = []
                for r, other_id in refs[:limit]:
                    other = db.entity(other_id)
                    if other is None:
                        continue
                    items.append(f"<li>{self.eref(other)} &mdash; {self.srcref(r.file_id, r.line)}</li>")
                more = f"<li>… {len(refs) - limit} more</li>" if len(refs) > limit else ""
                s.append(f"<div><b>{esc(label)}</b><ul>{''.join(items)}{more}</ul></div>")
            s.append("</div>")
            parts.append("".join(s))
        self.page("xref.html", "Cross reference", "".join(parts))

    def _metric_table(self, tid, kinds, names):
        ents, vals = self.db.metric_table(kinds)
        head = "".join(f"<th class=n title='{esc(METRIC_DESCRIPTIONS.get(n, ''))}'>{esc(n)}</th>" for n in names)
        rows = []
        for e in ents:
            if e.file_id is None:
                continue
            m = vals.get(e.id, {})
            cells = "".join(f"<td class=n>{_fmt(m.get(n))}</td>" for n in names)
            rows.append(f"<tr><td>{self.eref(e)}</td><td>{self.srcref(e.file_id, e.line)}</td>{cells}</tr>")
        return (f"<input class=filter data-table={tid} placeholder='Filter…'><table id={tid} class=sortable>"
                f"<thead><tr><th>Entity</th><th>Location</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")

    def metrics(self):
        body = "<h2>Function metrics</h2>" + self._metric_table("fm", sorted(M.FUNCTION_KINDS), FUNC_METRICS)
        body += "<h2>Class metrics</h2>" + self._metric_table("cm", sorted(M.CLASS_KINDS), CLASS_METRICS)
        body += "<h2>Metric definitions</h2><table>" + "".join(
            f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in METRIC_DESCRIPTIONS.items()) + "</table>"
        self.page("metrics.html", "Metrics", body)

    def files_page(self):
        db = self.db
        files, vals = db.metric_table([M.FILE])
        head = "".join(f"<th class=n>{esc(n)}</th>" for n in FILE_METRICS)
        rows = []
        for e in files:
            m = vals.get(e.id, {})
            f = db.file(e.file_id)
            cells = "".join(f"<td class=n>{_fmt(m.get(n))}</td>" for n in FILE_METRICS)
            rows.append(f"<tr><td>{self.srcref(e.file_id, 1, f.relpath)}</td><td>{esc(display_name(f.lang))}</td>{cells}</tr>")
        body = ("<h2>Files</h2><input class=filter data-table=files placeholder='Filter files…'>"
                f"<table id=files class=sortable><thead><tr><th>File</th><th>Language</th>{head}</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table>")
        self.page("files.html", "Files", body)

    def codecheck(self):
        vs = run_codecheck(self.db)
        counts = {}
        for v in vs:
            counts[v.rule] = counts.get(v.rule, 0) + 1
        body = "<h2>Rule summary</h2><table class=sortable><thead><tr><th>Rule</th><th>Title</th><th>Severity</th>" \
               "<th class=n>Violations</th></tr></thead><tbody>"
        for rid, n in sorted(counts.items(), key=lambda x: -x[1]):
            r = RULES_BY_ID[rid]
            body += (f"<tr><td>{rid}</td><td>{esc(r.title)}</td><td class='sev-{r.severity}'>{r.severity}</td>"
                     f"<td class=n>{n}</td></tr>")
        body += "</tbody></table><h2>Violations</h2><input class=filter data-table=cc placeholder='Filter…'>"
        body += "<table id=cc class=sortable><thead><tr><th>Severity</th><th>Rule</th><th>Location</th><th>Message</th>" \
                "</tr></thead><tbody>"
        for v in vs:
            body += (f"<tr><td class='sev-{v.severity}'>{v.severity}</td><td>{v.rule}</td>"
                     f"<td>{self.srcref(v.file_id, v.line) if v.file_id else ''}</td><td>{esc(v.message)}</td></tr>")
        body += "</tbody></table>"
        self.page("codecheck.html", "CodeCheck", body)

    def graphs(self):
        db = self.db
        links = {e.id: f"xref.html#e{e.id}" for e in self._entities()}
        body = "<h2>Architecture</h2><div class=graph>" + to_svg(architecture_graph(db)) + "</div>"
        g = uml_graph(db, None, max_classes=40)
        if g.nodes:
            body += "<h2>Class diagram</h2><div class=graph>" + to_svg(g, links) + "</div>"
        roots = [c for c in db.entities(kinds=sorted(M.CLASS_KINDS))
                 if c.file_id and not db.related(c.id, (M.INHERIT, M.IMPLEMENT)) and
                 db.related(c.id, (M.INHERIT, M.IMPLEMENT), reverse=True)]
        for c in roots[:20]:
            body += f"<h2>Inheritance: {esc(c.longname)}</h2><div class=graph>" + \
                to_svg(inheritance_graph(db, c), links) + "</div>"
        self.page("graphs.html", "Graphs", body)

    def sources(self):
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name

        db = self.db
        fmt = HtmlFormatter(nowrap=True)
        style = HtmlFormatter(style="default").get_style_defs(".src")
        with open(os.path.join(self.out, "style.css"), "a", encoding="utf-8") as fh:
            fh.write(style)
        for f in db.files():
            try:
                with open(f.path, encoding="utf-8", errors="replace") as fh:
                    code = fh.read()
            except OSError:
                continue
            lexer = get_lexer_by_name(LANGUAGES[f.lang].lexer if f.lang in LANGUAGES else "text", stripnl=False)
            lines = highlight(code, lexer, fmt).split("\n")
            rows = "".join(f"<tr id=L{i}><td class=ln>{i}</td><td><pre style='margin:0'>{ln or ' '}</pre></td></tr>"
                           for i, ln in enumerate(lines, 1))
            body = f"<h2>{esc(f.relpath)}</h2><div class=src><table>{rows}</table></div>"
            self.page(f"src/f{f.id}.html", f.relpath, body)
            # fix asset paths for nested page
            p = os.path.join(self.out, "src", f"f{f.id}.html")
            with open(p, encoding="utf-8") as fh:
                txt = fh.read()
            txt = txt.replace("href='style.css'", "href='../style.css'").replace("src='report.js'", "src='../report.js'")
            for target in ("index.html", "dictionary.html", "metrics.html", "codecheck.html", "graphs.html", "files.html"):
                txt = txt.replace(f'href="{target}"', f'href="../{target}"')
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(txt)


def generate_html(db, outdir, include_source=True, progress=None) -> str:
    return HtmlReport(db, outdir, include_source, progress).generate()


def metrics_csv(db, kinds=None, names=None) -> str:
    kinds = kinds or sorted(M.FUNCTION_KINDS | M.CLASS_KINDS | {M.FILE})
    ents, vals = db.metric_table(kinds)
    if names is None:
        names = sorted({n for v in vals.values() for n in v})
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Kind", "Name", "File", "Line"] + names)
    for e in ents:
        if e.file_id is None:
            continue
        f = db.file(e.file_id)
        m = vals.get(e.id, {})
        w.writerow([e.kind, e.longname, f.relpath if f else "", e.line] + [_fmt(m.get(n)) for n in names])
    return buf.getvalue()
