"""Compile swhforest patterns to ast-grep rules, so candidates can be verified by ast-grep (Rust).

Semantics follow treematch.node_match:
  type      -> kind (the type must be named-only in the grammar)
  text      -> exact regex on a leaf
  fields    -> one direct child in that grammar field matches the sub-pattern
  contains  -> the node itself or a descendant matches the sub-pattern
A pattern that cannot be expressed exactly (untyped node with constraints, anonymous or
ambiguous types) compiles to None, and the caller falls back to the Python matcher.
"""

from __future__ import annotations

import re

from .query import Pattern
from .structindex import named_only_types

# catalog language -> ast-grep language name
AST_GREP_LANG = {"python": "python", "javascript": "javascript", "typescript": "typescript",
                 "java": "java", "c": "c", "cpp": "cpp", "csharp": "csharp", "go": "go",
                 "rust": "rust", "php": "php"}


class Unsupported(Exception):
    pass


def _rule(p: Pattern, ok: frozenset) -> dict:
    if p.type is None or p.type not in ok:
        raise Unsupported(p.type)
    rule: dict = {"kind": p.type}
    parts = []
    if p.text is not None:
        if p.fields or p.contains is not None:
            raise Unsupported("text with children")
        rule["regex"] = "^" + re.escape(p.text.decode()) + "$"
    for fname, sub in p.fields.items():
        parts.append({"has": dict(_rule(sub, ok), field=fname)})
    if p.contains is not None:
        c = _rule(p.contains, ok)
        parts.append({"any": [c, {"has": dict(c, stopBy="end")}]})
    if parts:
        rule["all"] = parts
    return rule


def compile_pattern(p: Pattern, language: str) -> dict | None:
    """ast-grep rule equivalent to ``p``, or None if it cannot be expressed exactly."""
    try:
        return _rule(p, named_only_types(language))
    except Unsupported:
        return None


def count_matches(src: bytes, language: str, rule: dict, path: str | None = None) -> int:
    from ast_grep_py import SgRoot
    lang = AST_GREP_LANG[language]
    if language == "typescript" and path and path.lower().endswith(".tsx"):
        lang = "tsx"
    return len(SgRoot(src.decode("utf-8", "replace"), lang).root().find_all({"rule": rule}))
