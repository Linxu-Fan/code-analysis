import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SAMPLES = os.path.join(ROOT, "tests", "samples")

from codescope.core.analyzer import Analyzer, create_project  # noqa: E402


def _build(tmp_path, sub, name):
    db = create_project(str(tmp_path / f"{name}.csdb"), name, [os.path.join(SAMPLES, sub)])
    Analyzer(db).analyze()
    return db


@pytest.fixture
def cpp_db(tmp_path):
    db = _build(tmp_path, "cpp", "cpp")
    yield db
    db.close()


@pytest.fixture
def py_db(tmp_path):
    db = _build(tmp_path, "python", "py")
    yield db
    db.close()


@pytest.fixture
def all_db(tmp_path):
    db = create_project(str(tmp_path / "all.csdb"), "all", [SAMPLES])
    Analyzer(db).analyze()
    yield db
    db.close()


def one(db, longname):
    hits = [e for e in db.entities(include_locals=True) if e.longname == longname]
    assert hits, f"entity {longname} not found"
    return hits[0]
