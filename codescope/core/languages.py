"""Language registry: maps file extensions to tree-sitter grammars."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Parser


@dataclass(frozen=True)
class LanguageInfo:
    name: str            # display name, e.g. "C++"
    key: str             # internal key, e.g. "cpp"
    extensions: tuple
    family: str          # languages in one family can reference each other (C/C++)
    lexer: str           # pygments lexer alias for syntax highlighting


LANGUAGES = {
    "c": LanguageInfo("C", "c", (".c",), "c", "c"),
    "cpp": LanguageInfo("C++", "cpp", (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".inl", ".tpp"), "c", "cpp"),
    "python": LanguageInfo("Python", "python", (".py", ".pyw", ".pyi"), "python", "python"),
}

# Header files are shared between C and C++; resolved per project.
HEADER_EXTENSIONS = (".h",)

# User-selectable languages.
SELECTABLE = ["c", "cpp", "python"]


def language_for_path(path: str, enabled=None, header_lang: str = "cpp"):
    """Return the language key for *path* or None if it is not analyzable."""
    ext = os.path.splitext(path)[1].lower()
    if ext in HEADER_EXTENSIONS:
        key = header_lang
        if enabled is not None and key not in enabled:
            key = "c" if "c" in enabled else ("cpp" if "cpp" in enabled else None)
        return key
    for key, info in LANGUAGES.items():
        if ext in info.extensions:
            if enabled is not None and key not in enabled:
                return None
            return key
    return None


@lru_cache(maxsize=None)
def get_language(key: str) -> Language:
    if key == "c":
        import tree_sitter_c as mod
    elif key == "cpp":
        import tree_sitter_cpp as mod
    elif key == "python":
        import tree_sitter_python as mod
    else:
        raise KeyError(key)
    return Language(mod.language())


def get_parser(key: str) -> Parser:
    return Parser(get_language(key))


def family(key: str) -> str:
    info = LANGUAGES.get(key)
    return info.family if info else key


def display_name(key: str) -> str:
    info = LANGUAGES.get(key)
    return info.name if info else key
