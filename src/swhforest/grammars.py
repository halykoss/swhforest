"""Registry of the tree-sitter grammars used by SWHForest.

A catalog ``language`` (lower-case Stack-Edu name) maps to one grammar, except
TypeScript, whose ``.tsx`` files are parsed with the TSX grammar.
"""

from __future__ import annotations

import importlib

from tree_sitter import Language, Parser

# grammar name -> (python module, function returning the language pointer)
GRAMMARS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "java": ("tree_sitter_java", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "csharp": ("tree_sitter_c_sharp", "language"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
    "php": ("tree_sitter_php", "language_php"),
}

# Stack-Edu config name -> catalog language
STACK_EDU = {"Python": "python", "JavaScript": "javascript", "Java": "java", "TypeScript": "typescript",
             "C": "c", "Cpp": "cpp", "CSharp": "csharp", "Go": "go", "Rust": "rust", "PHP": "php"}

# catalog language -> every grammar its files may be parsed with
LANGUAGE_GRAMMARS = {lang: (lang,) for lang in STACK_EDU.values()}
LANGUAGE_GRAMMARS["typescript"] = ("typescript", "tsx")

_cache: dict[str, Language] = {}


def grammar_for(language: str, path: str | None = None) -> str:
    language = {"py": "python", "js": "javascript"}.get(language, language)
    if language == "typescript" and path and path.lower().endswith(".tsx"):
        return "tsx"
    return language


def get_language(grammar: str) -> Language:
    if grammar not in _cache:
        if grammar not in GRAMMARS:
            raise ValueError(f"unsupported grammar: {grammar}")
        mod, fn = GRAMMARS[grammar]
        _cache[grammar] = Language(getattr(importlib.import_module(mod), fn)())
    return _cache[grammar]


def make_parser(language: str = "python", path: str | None = None) -> Parser:
    return Parser(get_language(grammar_for(language, path)))
