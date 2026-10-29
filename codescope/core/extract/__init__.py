"""Per-language extractors."""

from __future__ import annotations


def extractor_class(lang: str):
    if lang == "python":
        from .python import PythonExtractor
        return PythonExtractor
    if lang in ("c", "cpp"):
        from .cfamily import CExtractor, CppExtractor
        return CExtractor if lang == "c" else CppExtractor
    raise KeyError(lang)
