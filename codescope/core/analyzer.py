"""Project analysis driver: scan sources, extract in parallel, store and link."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from . import model as M
from .database import Database
from .extract import extractor_class
from .languages import get_parser, language_for_path
from .metrics import compute_derived
from .resolver import link

MAX_FILE_SIZE = 8 * 1024 * 1024
# Bump when the extractors record new information, so existing projects are fully re-parsed.
EXTRACTOR_VERSION = "4"


class Cancelled(Exception):
    pass


# "#define FOO_EXPORT", "#define API __declspec(dllexport)", "#define X __attribute__((visibility(...)))"
_ATTR_DEFINE = re.compile(rb"^[ \t]*#[ \t]*define[ \t]+([A-Z_][A-Z0-9_]*)[ \t]*"
                          rb"(?:__declspec[ \t]*\(.*\)|__attribute__[ \t]*\(\(.*\)\)|\[\[.*\]\])?[ \t]*$", re.M)
# "class FOO_API Name {" / "struct ALIGN(8) Name :"
_CLASS_MACRO = re.compile(rb"\b(?:class|struct|union)[ \t]+([A-Z_][A-Z0-9_]{2,})(?:[ \t]*\([^()\n]*\))?"
                          rb"[ \t]+(?=[A-Za-z_]\w*[ \t\n]*(?:final[ \t\n]*)?[:{])")


def find_attribute_macros(src: bytes) -> set:
    """Names of macros that expand to nothing or to a declaration attribute."""
    names = {m.group(1).decode() for m in _ATTR_DEFINE.finditer(src)}
    return {n for n in names if not n.endswith(("_H", "_H_", "_HPP", "_INCLUDED"))}


def scrub_macros(src: bytes, names) -> bytes:
    """Blank out annotation macros (keeping byte offsets) so tree-sitter sees plain C++."""
    def blank(m):
        return b" " * (m.end() - m.start())

    def blank_group(m):
        a, b = m.span(1)
        end = m.end()
        return m.group(0)[: a - m.start()] + b" " * (end - a)
    src = _CLASS_MACRO.sub(blank_group, src)
    if names:
        rx = re.compile(rb"(?<![#\w])\b(?:" + b"|".join(re.escape(n.encode()) for n in sorted(names)) +
                        rb")\b(?![ \t]*\()")
        lines = src.split(b"\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith(b"#"):
                continue
            lines[i] = rx.sub(blank, line)
        src = b"\n".join(lines)
    return src


def parse_file(path: str, lang: str, relpath: str, ignore_macros=()) -> M.FileResult:
    """Parse and extract one file. Runs in worker processes; must stay picklable."""
    try:
        with open(path, "rb") as fh:
            src = fh.read()
        if src.startswith(b"\xef\xbb\xbf"):
            src = src[3:]
        src = src.replace(b"\r\n", b"\n")
        if lang in ("c", "cpp"):
            src = scrub_macros(src, ignore_macros)
        tree = get_parser(lang).parse(src)
        ex = extractor_class(lang)(path, lang, src, tree, relpath)
        return ex.run()
    except Exception as exc:   # pragma: no cover - defensive
        res = M.FileResult(path, lang)
        res.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        res.entities = [M.XEntity(0, M.FILE, os.path.basename(path), relpath, None, 1, 1, 1, 1)]
        return res


def _parse_task(args):
    return parse_file(*args)


def file_digest(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_sources(cfg: dict):
    """Yield (abs path, language) for every analyzable file under the project roots."""
    from .folders import matches, may_contain
    langs = set(cfg.get("languages") or [])
    excludes = cfg.get("exclude") or []
    selection = cfg.get("folders") or []
    found = []
    for root in cfg.get("roots", []):
        root = os.path.abspath(root)
        if os.path.isfile(root):
            found.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
            keep = []
            for d in dirnames:
                probe = "/" + (rel_dir + "/" if rel_dir != "." else "") + d + "/"
                if d.startswith(".") or any(fnmatch.fnmatch(probe, pat) or fnmatch.fnmatch(probe + "x", pat)
                                            for pat in excludes):
                    continue
                if not may_contain(os.path.join(dirpath, d), selection):
                    continue
                keep.append(d)
            dirnames[:] = sorted(keep)
            for f in sorted(filenames):
                full = os.path.join(dirpath, f)
                if matches(full, selection):
                    found.append(full)
    header = cfg.get("header_lang", "auto")
    if header == "auto":
        has_cpp = any(language_for_path(p, langs) == "cpp" and not p.lower().endswith(".h") for p in found)
        header = "cpp" if has_cpp or "c" not in langs else "c"
    out = []
    seen = set()
    for p in found:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        rel = "/" + ap.replace("\\", "/")
        if any(fnmatch.fnmatch(rel, pat) for pat in excludes):
            continue
        lang = language_for_path(p, langs, header)
        if lang:
            out.append((ap, lang))
    return out


class Analyzer:
    """Runs a full or incremental analysis of a project database."""

    def __init__(self, db: Database, progress=None, cancel=None):
        self.db = db
        self.progress = progress or (lambda stage, i, n, msg="": None)
        self.cancel = cancel or (lambda: False)
        self.stats = {}

    def _check(self):
        if self.cancel():
            raise Cancelled()

    def relpath(self, path):
        base = self.db.base_dir
        try:
            rel = os.path.relpath(path, base)
        except ValueError:
            rel = path
        if rel.startswith(".."):
            rel = path
        return rel.replace("\\", "/")

    def analyze(self, full: bool = False):
        t0 = time.time()
        db = self.db
        cfg = db.config()
        if db.get_meta("extractor") != EXTRACTOR_VERSION and db.files():
            full = True                     # parsed by an older version: re-parse everything
        self.progress("Scanning", 0, 1, "Scanning source directories")
        sources = scan_sources(cfg)
        known = {f.path: f for f in db.files()}
        wanted = {p for p, _ in sources}
        removed = [f for p, f in known.items() if p not in wanted]
        todo = []
        for path, lang in sources:
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_size > MAX_FILE_SIZE:
                continue
            row = known.get(path)
            if not full and row is not None and row.lang == lang and row.size == st.st_size \
                    and abs((row.mtime or 0) - st.st_mtime) < 1e-6:
                continue
            digest = file_digest(path)
            if not full and row is not None and row.hash == digest and row.lang == lang:
                db.conn.execute("UPDATE files SET mtime=? WHERE id=?", (st.st_mtime, row.id))
                continue
            todo.append((path, lang, self.relpath(path), digest, st.st_mtime, st.st_size))
        for f in removed:
            db.remove_file(f.id)
        db.commit()
        self._check()
        n = len(todo)
        workers = int(cfg.get("workers") or 0) or max(1, (os.cpu_count() or 2) - 1)
        macros = set(cfg.get("ignore_macros") or [])
        if todo and any(lang in ("c", "cpp") for _, lang in sources):
            self.progress("Scanning", 0, 1, "Collecting macro definitions")
            for path, lang in sources:
                if lang in ("c", "cpp"):
                    try:
                        with open(path, "rb") as fh:
                            data = fh.read()
                    except OSError:
                        continue
                    if b"define" in data:
                        macros |= find_attribute_macros(data.replace(b"\r\n", b"\n"))
        macros = tuple(sorted(macros))
        tasks = [(p, lang, rel, macros) for p, lang, rel, *_ in todo]
        meta = {p: (rel, dg, mt, sz) for p, lang, rel, dg, mt, sz in todo}
        done = 0
        errors = 0

        def store(res):
            nonlocal done, errors
            rel, dg, mt, sz = meta[res.path]
            db.store_result(res, rel, dg, mt, sz)
            if res.error:
                errors += 1
            done += 1
            self.progress("Parsing", done, n, rel)
            if done % 50 == 0:
                db.commit()

        if n > 40 and workers > 1:
            try:
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for res in pool.map(_parse_task, tasks, chunksize=8):
                        store(res)
                        self._check()
            except Cancelled:
                db.commit()
                raise
            except (OSError, RuntimeError, ImportError):
                # process pools can be unavailable (sandboxes, frozen apps); fall back
                for t in tasks[done:]:
                    store(parse_file(*t))
                    self._check()
        else:
            for t in tasks:
                store(parse_file(*t))
                self._check()
        db.commit()
        if n or removed or full or db.get_meta("linked") != "1":
            self.progress("Linking", 0, 1, "Resolving references")
            link(db, self.progress)
            self.progress("Metrics", 0, 1, "Computing metrics")
            compute_derived(db)
            db.set_meta("linked", "1")
        db.set_meta("extractor", EXTRACTOR_VERSION)
        db.set_meta("analyzed_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.invalidate()
        self.stats = {"parsed": n, "removed": len(removed), "errors": errors,
                      "files": len(sources), "seconds": round(time.time() - t0, 2)}
        self.progress("Done", 1, 1, "Analysis complete")
        return self.stats


def pending_changes(cfg, known):
    """Files an incremental analysis would pick up now: (changed, added, removed) path lists.

    *known* maps the paths of the analyzed files to (lang, size, mtime), as stored in the
    project.  Uses the same rules as Analyzer.analyze, so a reported change always disappears
    after the next analysis.
    """
    changed, added, wanted = [], [], set()
    for path, lang in scan_sources(cfg):
        wanted.add(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size > MAX_FILE_SIZE:
            continue
        row = known.get(path)
        if row is None:
            added.append(path)
        elif row[0] != lang or row[1] != st.st_size or abs((row[2] or 0) - st.st_mtime) >= 1e-6:
            changed.append(path)
    removed = [p for p in known if p not in wanted]
    return changed, added, removed


def create_project(db_path, name, roots, languages=None, exclude=None) -> Database:
    cfg = {"name": name, "roots": [os.path.abspath(r) for r in roots]}
    if languages:
        cfg["languages"] = list(languages)
    if exclude is not None:
        cfg["exclude"] = list(exclude)
    return Database.create(db_path, cfg)
