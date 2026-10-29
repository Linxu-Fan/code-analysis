"""Folder selection: scan a project's directory tree and filter sources to chosen folders.

A selection is a list of ``{"path": <absolute dir>, "recursive": bool}`` entries stored in the
project config under ``folders``.  ``recursive`` selects a whole subtree; non-recursive entries
select only the files directly inside a directory.  An empty selection means "everything".
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field

from .languages import language_for_path

# Directory names that usually hold code you did not write.
THIRD_PARTY_NAMES = frozenset({
    "third_party", "thirdparty", "third-party", "3rdparty", "3rd_party", "3rd-party", "external", "externals",
    "extern", "vendor", "vendors", "deps", "dependencies", "contrib", "submodules", "packages", "libs_external",
    "node_modules", "build", "builds", "out", "bin", "obj", "cmake-build-debug", "cmake-build-release",
    "intermediate", "binaries", "dist", "site-packages", "venv", ".venv", "__pycache__",
})


@dataclass
class FolderNode:
    name: str
    path: str                      # absolute, forward slashes
    files: int = 0                 # analyzable files directly in this folder
    total: int = 0                 # analyzable files in the subtree
    children: list = field(default_factory=list)

    @property
    def third_party(self):
        return self.name.lower() in THIRD_PARTY_NAMES


def norm(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/").rstrip("/")


def _excluded(rel_probe, excludes):
    return any(fnmatch.fnmatch(rel_probe, pat) or fnmatch.fnmatch(rel_probe + "x", pat) for pat in excludes)


def scan_tree(root: str, languages=None, excludes=(), max_dirs=50000) -> FolderNode:
    """Directory tree under *root* with counts of analyzable files (hidden/excluded dirs skipped)."""
    root = norm(root)
    langs = set(languages) if languages else None
    nodes = {root: FolderNode(os.path.basename(root) or root, root)}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dp = dirpath.replace("\\", "/").rstrip("/")
        node = nodes.get(dp)
        if node is None:
            dirnames[:] = []
            continue
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        keep = []
        for d in sorted(dirnames, key=str.lower):
            probe = "/" + (rel + "/" if rel != "." else "") + d + "/"
            if d.startswith(".") or _excluded(probe, excludes):
                continue
            keep.append(d)
            count += 1
            if count > max_dirs:
                break
            child = FolderNode(d, dp + "/" + d)
            nodes[child.path] = child
            node.children.append(child)
        dirnames[:] = keep
        node.files = sum(1 for f in filenames if language_for_path(f, langs) is not None)

    def total(n):
        n.total = n.files + sum(total(c) for c in n.children)
        return n.total
    total(nodes[root])
    return nodes[root]


def default_selection(tree: FolderNode):
    """Everything except folders that look like third-party / build output."""
    sel = []

    def walk(n, top=False):
        if n.total == 0 or (n.third_party and not top):
            return
        if not any(c.third_party and c.total for c in _all(n)):
            sel.append({"path": n.path, "recursive": True})
            return
        if n.files:
            sel.append({"path": n.path, "recursive": False})
        for c in n.children:
            walk(c)
    walk(tree, top=True)
    return sel


def _all(n):
    for c in n.children:
        yield c
        yield from _all(c)


def matches(path: str, selection) -> bool:
    """True if a file *path* is inside the folder *selection* (empty selection = everything)."""
    if not selection:
        return True
    p = norm(path)
    d = os.path.dirname(p)
    for s in selection:
        sp = norm(s["path"])
        if s.get("recursive", True):
            if d == sp or d.startswith(sp + "/"):
                return True
        elif d == sp:
            return True
    return False


def may_contain(dirpath: str, selection) -> bool:
    """True if a directory can hold selected files (used to prune the walk)."""
    if not selection:
        return True
    d = norm(dirpath)
    for s in selection:
        sp = norm(s["path"])
        if d == sp or sp.startswith(d + "/") or (s.get("recursive", True) and d.startswith(sp + "/")):
            return True
    return False


def describe(selection, roots=()) -> str:
    """Short human readable summary, e.g. 'src/, include/'."""
    if not selection:
        return "all folders"
    out = []
    for s in selection:
        p = norm(s["path"])
        for r in roots:
            r = norm(r)
            if p == r or p.startswith(r + "/"):
                p = os.path.relpath(p, os.path.dirname(r)).replace("\\", "/")
                break
        out.append(p + ("/" if s.get("recursive", True) else " (files only)"))
    return ", ".join(out)
