import json
import os

from codescope.cli import main
from codescope.core.codecheck import RULES, run_codecheck
from codescope.core.report import generate_html, metrics_csv

from .conftest import SAMPLES


def test_codecheck(all_db):
    vs = run_codecheck(all_db)
    found = {(v.rule, os.path.basename(v.path), v.line) for v in vs}
    assert ("DEAD-01", "util.c", 17) in found       # unused_function
    assert ("STMT-01", "util.c", 18) in found       # goto
    assert ("DEAD-01", "main.py", 24) in found      # unused_helper
    assert not any(v.rule == "DEAD-01" and "main" == v.entity for v in vs)
    strict = run_codecheck(all_db, enabled={"CMPLX-01"}, thresholds={"CMPLX-01": 3})
    assert {v.entity for v in strict} >= {"classify", "main.build"}
    assert len({r.id for r in RULES}) == len(RULES)


def test_codecheck_ast_rules(tmp_path):
    from codescope.core.analyzer import Analyzer, create_project
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "bad.py").write_text(
        "from os import *\n\ndef f(a=[]):\n    try:\n        pass\n    except:\n        pass\n"
        "    if a == None:\n        return 1\n")
    (tmp_path / "s" / "bad.cpp").write_text(
        "int g(int x) {\n  int y;\n  if (y = x) {}\n  switch (x) { case 1: break; }\n"
        "  try { } catch (...) { }\n  return y;\n}\n")
    db = create_project(str(tmp_path / "b.csdb"), "b", [str(tmp_path / "s")])
    Analyzer(db).analyze()
    rules = {v.rule for v in run_codecheck(db)}
    assert {"STMT-02", "STMT-03", "STMT-04", "STMT-05", "STMT-06", "STMT-07", "STMT-08"} <= rules
    db.close()


def test_report_and_csv(all_db, tmp_path):
    out = tmp_path / "report"
    index = generate_html(all_db, str(out))
    assert os.path.exists(index)
    for page in ("dictionary.html", "xref.html", "metrics.html", "codecheck.html", "graphs.html", "files.html"):
        assert (out / page).exists()
    assert len(list((out / "src").iterdir())) == 8
    assert "geo::Shape" in (out / "xref.html").read_text(encoding="utf-8")
    csv_text = metrics_csv(all_db)
    assert csv_text.splitlines()[0].startswith("Kind,Name,File,Line")
    assert "classify" in csv_text


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "cli.csdb")
    assert main(["create", db, "-r", os.path.join(SAMPLES, "cpp"), "-n", "demo", "-a"]) == 0
    assert main(["list", db, "-k", "class"]) == 0
    out = capsys.readouterr().out
    assert "geo::Shape" in out
    assert main(["info", db, "geo::Rect::area"]) == 0
    assert "declared  : include/shape.h:36" in capsys.readouterr().out
    assert main(["check", db, "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(v["rule"] == "STMT-01" for v in data)
    assert main(["check", db, "--fail-on", "warning"]) == 1
    capsys.readouterr()
    svg = str(tmp_path / "g.svg")
    assert main(["graph", db, "main", "-t", "calls", "-o", svg]) == 0
    assert open(svg, encoding="utf-8").read().startswith("<svg")
    capsys.readouterr()
    assert main(["metrics", db, "--project"]) == 0
    assert json.loads(capsys.readouterr().out)["Files"] == 5
    assert main(["report", db, "-o", str(tmp_path / "r"), "-q"]) == 0
    assert main(["analyze", db, "-q"]) == 0
    assert "0 parsed" in capsys.readouterr().out
