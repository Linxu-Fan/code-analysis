"""SQLite backed project database (entities, references, metrics, config)."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from . import model as M

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE, relpath TEXT, lang TEXT,
    hash TEXT, mtime REAL, size INTEGER, entity_id INTEGER, error TEXT, parse_errors INTEGER);
CREATE TABLE IF NOT EXISTS entities(
    id INTEGER PRIMARY KEY, kind TEXT, name TEXT, longname TEXT, lang TEXT,
    file_id INTEGER, parent0 INTEGER, parent_id INTEGER,
    line INTEGER, col INTEGER, end_line INTEGER, end_col INTEGER,
    signature TEXT, modifiers TEXT, type TEXT, owner TEXT, alias_of INTEGER);
CREATE TABLE IF NOT EXISTS refs(
    id INTEGER PRIMARY KEY, kind TEXT, src_id INTEGER, dst_id INTEGER,
    name TEXT, qualifier TEXT, file_id INTEGER, line INTEGER, col INTEGER, detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS metrics(
    entity_id INTEGER, name TEXT, value REAL, PRIMARY KEY(entity_id, name)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_ent_name ON entities(name);
CREATE INDEX IF NOT EXISTS ix_ent_file ON entities(file_id);
CREATE INDEX IF NOT EXISTS ix_ent_parent ON entities(parent_id);
CREATE INDEX IF NOT EXISTS ix_ent_kind ON entities(kind);
CREATE INDEX IF NOT EXISTS ix_ref_src ON refs(src_id);
CREATE INDEX IF NOT EXISTS ix_ref_dst ON refs(dst_id);
CREATE INDEX IF NOT EXISTS ix_ref_file ON refs(file_id, line);
"""

ENTITY_COLS = ("id, kind, name, longname, lang, file_id, parent_id, line, col, end_line, end_col, "
               "signature, modifiers, type, owner, alias_of")
REF_COLS = "id, kind, src_id, dst_id, name, qualifier, file_id, line, col, detail"


@dataclass
class Entity:
    id: int
    kind: str
    name: str
    longname: str
    lang: str
    file_id: int | None
    parent_id: int | None
    line: int
    col: int
    end_line: int
    end_col: int
    signature: str
    modifiers: str
    type: str
    owner: str
    alias_of: int | None

    @property
    def is_unresolved(self):
        return self.kind in M.UNRESOLVED_KINDS

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, Entity) and other.id == self.id


@dataclass
class Ref:
    id: int
    kind: str
    src_id: int
    dst_id: int | None
    name: str
    qualifier: str
    file_id: int
    line: int
    col: int
    detail: str = ""

    @property
    def via(self) -> str:
        """Function through which this Set writes (output argument, returned reference), or ''."""
        d = self.detail or ""
        return d[len(M.VIA_PREFIX):].split("\t", 1)[0] if d.startswith(M.VIA_PREFIX) else ""

    @property
    def alias(self) -> str:
        """Local reference or pointer through which this Set writes, or ''."""
        d = self.detail or ""
        return d[len(M.ALIAS_PREFIX):].split("\t", 1)[0] if d.startswith(M.ALIAS_PREFIX) else ""

    @property
    def alias_how(self) -> str:
        """How an alias was written: '' the alias itself, 'part' a member or element, or a mutating call."""
        parts = (self.detail or "").split("\t")
        return parts[2] if self.alias and len(parts) > 2 else ""


@dataclass
class FileRow:
    id: int
    path: str
    relpath: str
    lang: str
    hash: str
    mtime: float
    size: int
    entity_id: int
    error: str
    parse_errors: int


DEFAULT_CONFIG = {
    "name": "Untitled",
    "roots": [],
    "exclude": ["*/.git/*", "*/build/*", "*/__pycache__/*", "*/node_modules/*", "*/.venv/*", "*/venv/*"],
    "languages": ["c", "cpp", "python"],
    "header_lang": "auto",
    "ignore_macros": [],
    "folders": [],
    "workers": 0,
    "codecheck": {},
}


class Database:
    def __init__(self, path: str, create: bool = False):
        self.path = os.path.abspath(path)
        if not create and not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        if not any(r[1] == "detail" for r in self.conn.execute("PRAGMA table_info(refs)")):
            # project made by an older version; the next analysis fills the column (EXTRACTOR_VERSION)
            self.conn.execute("ALTER TABLE refs ADD COLUMN detail TEXT DEFAULT ''")
            self.set_meta("schema", str(SCHEMA_VERSION))
        self._ent_cache = {}
        self._file_cache = {}
        if create:
            self.set_meta("schema", str(SCHEMA_VERSION))

    # ---- lifecycle --------------------------------------------------------------
    @classmethod
    def create(cls, path: str, config: dict | None = None) -> "Database":
        if os.path.exists(path):
            os.remove(path)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
        db = cls(path, create=True)
        cfg = dict(DEFAULT_CONFIG)
        if config:
            cfg.update(config)
        cfg["roots"] = [os.path.abspath(r) for r in cfg.get("roots", [])]
        db.set_config(cfg)
        return db

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def commit(self):
        self.conn.commit()

    def invalidate(self):
        self._ent_cache.clear()
        self._file_cache.clear()

    # ---- meta / config -------------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
        self.conn.commit()

    def config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        raw = self.get_meta("config")
        if raw:
            cfg.update(json.loads(raw))
        return cfg

    def set_config(self, cfg: dict):
        self.set_meta("config", json.dumps(cfg, indent=1))

    @property
    def base_dir(self) -> str:
        """Directory used for relative paths: common root of the source roots."""
        roots = self.config().get("roots") or [os.path.dirname(self.path)]
        if len(roots) == 1:
            return roots[0]
        try:
            return os.path.commonpath(roots)
        except ValueError:
            return roots[0]

    # ---- files ----------------------------------------------------------------------
    def files(self) -> list[FileRow]:
        rows = self.conn.execute("SELECT id, path, relpath, lang, hash, mtime, size, entity_id, error, "
                                 "parse_errors FROM files ORDER BY relpath").fetchall()
        return [FileRow(*r) for r in rows]

    def file(self, file_id) -> FileRow | None:
        if file_id in self._file_cache:
            return self._file_cache[file_id]
        r = self.conn.execute("SELECT id, path, relpath, lang, hash, mtime, size, entity_id, error, "
                              "parse_errors FROM files WHERE id=?", (file_id,)).fetchone()
        f = FileRow(*r) if r else None
        self._file_cache[file_id] = f
        return f

    def file_by_path(self, path) -> FileRow | None:
        r = self.conn.execute("SELECT id FROM files WHERE path=?", (os.path.abspath(path),)).fetchone()
        return self.file(r[0]) if r else None

    def remove_file(self, file_id):
        c = self.conn
        c.execute("DELETE FROM metrics WHERE entity_id IN (SELECT id FROM entities WHERE file_id=?)", (file_id,))
        c.execute("DELETE FROM refs WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM entities WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM files WHERE id=?", (file_id,))

    def store_result(self, res: M.FileResult, relpath: str, digest: str, mtime: float, size: int):
        """Replace everything known about one file with a fresh extraction result."""
        c = self.conn
        path = os.path.abspath(res.path)
        row = c.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if row:
            fid = row[0]
            c.execute("DELETE FROM metrics WHERE entity_id IN (SELECT id FROM entities WHERE file_id=?)", (fid,))
            c.execute("DELETE FROM refs WHERE file_id=?", (fid,))
            c.execute("DELETE FROM entities WHERE file_id=?", (fid,))
            c.execute("UPDATE files SET relpath=?, lang=?, hash=?, mtime=?, size=?, error=?, parse_errors=? "
                      "WHERE id=?", (relpath, res.lang, digest, mtime, size, res.error, res.parse_errors, fid))
        else:
            cur = c.execute("INSERT INTO files(path, relpath, lang, hash, mtime, size, error, parse_errors) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (path, relpath, res.lang, digest, mtime, size, res.error, res.parse_errors))
            fid = cur.lastrowid
        start = (c.execute("SELECT COALESCE(MAX(id), 0) FROM entities").fetchone()[0]) + 1
        ids = {e.lid: start + i for i, e in enumerate(res.entities)}
        rows = []
        for e in res.entities:
            pid = ids.get(e.parent) if e.parent is not None else None
            longname = relpath if e.kind == M.FILE else e.longname
            rows.append((ids[e.lid], e.kind, e.name, longname, res.lang, fid, pid, pid, e.line, e.col,
                         e.end_line, e.end_col, e.signature, e.modifiers, e.type, e.owner))
        c.executemany("INSERT INTO entities(id, kind, name, longname, lang, file_id, parent0, parent_id, line, "
                      "col, end_line, end_col, signature, modifiers, type, owner) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.executemany("INSERT INTO refs(kind, src_id, name, qualifier, file_id, line, col, detail) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      [(r.kind, ids[r.src], r.name, r.qualifier, fid, r.line, r.col, r.detail) for r in res.refs])
        mrows = []
        for lid, ms in res.metrics.items():
            for k, v in ms.items():
                if not k.startswith("_"):
                    mrows.append((ids[lid], k, v))
        c.executemany("INSERT OR REPLACE INTO metrics(entity_id, name, value) VALUES(?,?,?)", mrows)
        file_ent = ids[0] if res.entities else None
        c.execute("UPDATE files SET entity_id=? WHERE id=?", (file_ent, fid))
        self._file_cache.pop(fid, None)
        return fid

    # ---- entities ---------------------------------------------------------------------
    def _ents(self, sql, args=()):
        out = []
        for r in self.conn.execute(sql, args):
            e = Entity(*r)
            self._ent_cache[e.id] = e
            out.append(e)
        return out

    def entity(self, eid) -> Entity | None:
        if eid is None:
            return None
        e = self._ent_cache.get(eid)
        if e is None:
            r = self._ents(f"SELECT {ENTITY_COLS} FROM entities WHERE id=?", (eid,))
            e = r[0] if r else None
        if e is not None and e.alias_of:
            return self.entity(e.alias_of)
        return e

    def entities(self, kinds=None, name=None, like=None, file_id=None, limit=None,
                 include_locals=True) -> list[Entity]:
        sql = f"SELECT {ENTITY_COLS} FROM entities WHERE alias_of IS NULL"
        args = []
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        if not include_locals:
            sql += " AND kind NOT IN (?, ?)"
            args += [M.PARAMETER, M.LOCAL]
        if name is not None:
            sql += " AND name=?"
            args.append(name)
        if like:
            sql += " AND (name LIKE ? OR longname LIKE ?)"
            args += [like, like]
        if file_id is not None:
            sql += " AND file_id=?"
            args.append(file_id)
        sql += " ORDER BY name COLLATE NOCASE, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self._ents(sql, args)

    def file_entity(self, file_id) -> Entity | None:
        f = self.file(file_id)
        return self.entity(f.entity_id) if f else None

    def entity_file(self, e: Entity) -> FileRow | None:
        return self.file(e.file_id) if e and e.file_id else None

    def children(self, eid, kinds=None) -> list[Entity]:
        sql = f"SELECT {ENTITY_COLS} FROM entities WHERE parent_id=? AND alias_of IS NULL"
        args = [eid]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        return self._ents(sql + " ORDER BY line, id", args)

    def declarations(self, eid) -> list[Entity]:
        """Entities merged into *eid* (e.g. C/C++ prototypes of a definition)."""
        return self._ents(f"SELECT {ENTITY_COLS} FROM entities WHERE alias_of=? ORDER BY file_id, line", (eid,))

    def parent(self, e: Entity) -> Entity | None:
        return self.entity(e.parent_id) if e.parent_id else None

    def enclosing(self, e: Entity, kinds) -> Entity | None:
        p = self.parent(e)
        while p is not None and p.kind not in kinds:
            p = self.parent(p)
        return p

    def file_outline(self, file_id) -> list[Entity]:
        return self._ents(f"SELECT {ENTITY_COLS} FROM entities WHERE file_id=? AND alias_of IS NULL "
                          "AND kind NOT IN (?, ?) ORDER BY line, col", (file_id, M.PARAMETER, M.LOCAL))

    def entity_at(self, file_id, line, col=None) -> Entity | None:
        """Best entity referenced or declared at a source position (1-based)."""
        best = None
        rows = self.conn.execute("SELECT dst_id, col, length(name) FROM refs WHERE file_id=? AND line=? "
                                 "AND dst_id IS NOT NULL AND detail NOT LIKE ?",
                                 (file_id, line, M.ALIAS_LIKE)).fetchall()
        for dst, c, ln in rows:
            if col is None or (c <= col <= c + max(ln, 1)):
                best = dst
                break
        if best is None:
            rows = self.conn.execute("SELECT id, col, length(name) FROM entities WHERE file_id=? AND line=? "
                                     "AND kind != ?", (file_id, line, M.FILE)).fetchall()
            for eid, c, ln in rows:
                if col is None or (c <= col <= c + max(ln, 1)):
                    best = eid
                    break
        return self.entity(best)

    def scope_at(self, file_id, line) -> Entity | None:
        rows = self._ents(f"SELECT {ENTITY_COLS} FROM entities WHERE file_id=? AND line<=? AND end_line>=? "
                          "AND kind IN (%s) AND alias_of IS NULL ORDER BY line DESC" %
                          ",".join("?" * len(M.FUNCTION_KINDS | M.CLASS_KINDS)),
                          (file_id, line, line, *sorted(M.FUNCTION_KINDS | M.CLASS_KINDS)))
        return rows[0] if rows else None

    # ---- references ------------------------------------------------------------------------
    def _refs(self, sql, args):
        return [Ref(*r) for r in self.conn.execute(sql, args)]

    def refs_from(self, eid, kinds=None, resolved=True) -> list[Ref]:
        sql = f"SELECT {REF_COLS} FROM refs WHERE src_id=?"
        args = [eid]
        if resolved:
            sql += " AND dst_id IS NOT NULL"
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        else:
            sql += " AND kind NOT IN (%s)" % ",".join("?" * len(M.LINKER_REF_KINDS))
            args += list(M.LINKER_REF_KINDS)
        return self._refs(sql + " ORDER BY line, col", args)

    def refs_to(self, eid, kinds=None) -> list[Ref]:
        sql = f"SELECT {REF_COLS} FROM refs WHERE dst_id=?"
        args = [eid]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        return self._refs(sql + " ORDER BY file_id, line, col", args)

    def refs_in_file(self, file_id) -> list[Ref]:
        return self._refs(f"SELECT {REF_COLS} FROM refs "
                          "WHERE file_id=? AND dst_id IS NOT NULL ORDER BY line, col", (file_id,))

    def related(self, eid, kinds, reverse=False) -> list[Entity]:
        """Distinct entities related to *eid* through references of *kinds*."""
        col_a, col_b = ("dst_id", "src_id") if reverse else ("src_id", "dst_id")
        sql = (f"SELECT DISTINCT {col_b} FROM refs WHERE {col_a}=? AND dst_id IS NOT NULL AND kind IN (%s)"
               % ",".join("?" * len(kinds)))
        out, seen = [], set()
        for (x,) in self.conn.execute(sql, (eid, *kinds)):
            e = self.entity(x)
            if e is not None and e.id not in seen and e.id != eid:
                seen.add(e.id)
                out.append(e)
        out.sort(key=lambda e: e.name.lower())
        return out

    # ---- metrics ------------------------------------------------------------------------------
    def metrics(self, eid) -> dict:
        return {k: v for k, v in self.conn.execute("SELECT name, value FROM metrics WHERE entity_id=?", (eid,))}

    def metric(self, eid, name, default=None):
        r = self.conn.execute("SELECT value FROM metrics WHERE entity_id=? AND name=?", (eid, name)).fetchone()
        return r[0] if r else default

    def metric_names(self, kinds=None) -> list[str]:
        sql = "SELECT DISTINCT m.name FROM metrics m"
        args = []
        if kinds:
            sql += " JOIN entities e ON e.id=m.entity_id WHERE e.kind IN (%s)" % ",".join("?" * len(kinds))
            args = list(kinds)
        return sorted(r[0] for r in self.conn.execute(sql, args))

    def metric_table(self, kinds) -> tuple[list[Entity], dict]:
        """Entities of *kinds* plus {entity_id: {metric: value}}."""
        ents = self.entities(kinds=kinds)
        vals = {}
        sql = ("SELECT m.entity_id, m.name, m.value FROM metrics m JOIN entities e ON e.id=m.entity_id "
               "WHERE e.alias_of IS NULL AND e.kind IN (%s)" % ",".join("?" * len(kinds)))
        for eid, name, v in self.conn.execute(sql, list(kinds)):
            vals.setdefault(eid, {})[name] = v
        return ents, vals

    def project_metrics(self) -> dict:
        out = {}
        q = self.conn.execute
        out["Files"] = q("SELECT COUNT(*) FROM files").fetchone()[0]
        for name in ("CountLine", "CountLineCode", "CountLineComment", "CountLineBlank", "CountLinePreprocessor",
                     "SumCyclomatic"):
            out[name] = int(q("SELECT COALESCE(SUM(m.value),0) FROM metrics m JOIN entities e ON e.id=m.entity_id "
                              "WHERE e.kind=? AND m.name=?", (M.FILE, name)).fetchone()[0])
        def count(kinds):
            return q("SELECT COUNT(*) FROM entities WHERE alias_of IS NULL AND kind IN (%s)" %
                     ",".join("?" * len(kinds)), list(kinds)).fetchone()[0]
        out["Classes"] = count(M.CLASS_KINDS)
        out["Functions"] = count(M.FUNCTION_KINDS)
        out["Variables"] = count({M.VARIABLE, M.FIELD})
        out["Entities"] = q("SELECT COUNT(*) FROM entities WHERE alias_of IS NULL").fetchone()[0]
        out["References"] = q("SELECT COUNT(*) FROM refs WHERE dst_id IS NOT NULL").fetchone()[0]
        code = out["CountLineCode"]
        out["RatioCommentToCode"] = round(out["CountLineComment"] / code, 2) if code else 0
        mx = q("SELECT MAX(m.value), AVG(m.value) FROM metrics m JOIN entities e ON e.id=m.entity_id "
               "WHERE e.alias_of IS NULL AND m.name='Cyclomatic'").fetchone()
        out["MaxCyclomatic"] = int(mx[0] or 0)
        out["AvgCyclomatic"] = round(mx[1] or 0, 2)
        langs = q("SELECT lang, COUNT(*) FROM files GROUP BY lang").fetchall()
        out["Languages"] = {l: n for l, n in langs}
        return out

    # ---- misc helpers -----------------------------------------------------------------------------
    def location(self, e: Entity) -> str:
        f = self.entity_file(e)
        if f is None:
            return ""
        return f"{f.relpath}:{e.line}"

    def source_path(self, e: Entity) -> str | None:
        f = self.entity_file(e)
        return f.path if f else None
