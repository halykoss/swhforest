"""Per-file AST features and shape hashes for the structural catalog.

A shape hash is a bottom-up Merkle hash of the tree with **leaf text erased**:
leaf = H(type), internal = H(type, child shapes). Two functions with the same
shape are isomorphic modulo identifiers/literals/comments. The same definition is
used by the DAG (``swhforest.dag``), so catalog and index agree.
"""

from __future__ import annotations

import hashlib
import json
import os
from array import array
from collections import Counter

from tree_sitter import Parser

LANG = {
    "python": {
        "functions": {"function_definition"},
        "classes": {"class_definition"},
        "methods": set(),  # function_definition inside a class
        "comments": {"comment"},
        "imports": {"import_statement", "import_from_statement"},
        "strings": {"string"},
        "identifiers": {"identifier"},
        "docstring": "python",
    },
    "javascript": {
        "functions": {
            "function_declaration", "function_expression", "arrow_function",
            "method_definition", "generator_function_declaration",
        },
        "classes": {"class_declaration", "class"},
        "methods": {"method_definition"},
        "comments": {"comment"},
        "imports": {"import_statement"},
        "strings": {"string", "template_string"},
        "identifiers": {
            "identifier", "property_identifier", "shorthand_property_identifier",
            "shorthand_property_identifier_pattern",
        },
        "docstring": "jsdoc",
    },
}


# further languages: one JSON spec per language in langspecs/ (lists become sets)
_SPEC_DIR = os.path.join(os.path.dirname(__file__), "langspecs")
if os.path.isdir(_SPEC_DIR):
    for _f in sorted(os.listdir(_SPEC_DIR)):
        if _f.endswith(".json"):
            _spec = json.load(open(os.path.join(_SPEC_DIR, _f)))
            _name = _spec.pop("language")
            LANG[_name] = {k: (set(v) if isinstance(v, list) and k != "doc_prefixes" else v)
                           for k, v in _spec.items() if not k.startswith("_")}

def _leaf(type_bytes: bytes, cache: dict) -> bytes:
    h = cache.get(type_bytes)
    if h is None:
        h = hashlib.blake2b(b"L" + type_bytes, digest_size=8).digest()
        cache[type_bytes] = h
    return h


def _internal(type_bytes: bytes, children: bytes) -> bytes:
    return hashlib.blake2b(b"I" + type_bytes + children, digest_size=8).digest()


def _python_docstring(node) -> bool:
    body = node.child_by_field_name("body")
    if body is None or body.named_child_count == 0:
        return False
    first = body.named_children[0]
    return first.type == "expression_statement" and first.named_child_count > 0 \
        and first.named_children[0].type == "string"


def _qualified_name(node) -> bool:
    """True if a C++ function definition declares a qualified name (C::m, ns::f)."""
    d = node.child_by_field_name("declarator")
    while d is not None and d.type != "function_declarator":
        nxt = d.child_by_field_name("declarator")
        if nxt is None and d.named_child_count:
            nxt = d.named_children[-1]
        d = nxt
    name = d.child_by_field_name("declarator") if d is not None else None
    return name is not None and name.type == "qualified_identifier"


def analyze(src: bytes, language: str, parser: Parser) -> tuple[dict, list[dict]]:
    cfg = LANG[language]
    tree = parser.parse(src)
    root = tree.root_node

    # preorder with parent and depth
    nodes = []
    parent = array("i")
    depth = array("i")
    stack = [(root, -1, 0)]
    while stack:
        n, p, d = stack.pop()
        i = len(nodes)
        nodes.append(n)
        parent.append(p)
        depth.append(d)
        kids = n.children
        for c in reversed(kids):
            stack.append((c, i, d + 1))
    n = len(nodes)

    # subtree end (preorder subtrees are contiguous)
    end = array("i", range(1, n + 1))
    for i in range(n - 1, -1, -1):
        p = parent[i]
        if p >= 0 and end[i] > end[p]:
            end[p] = end[i]

    # bottom-up shape hashes
    leaf_cache: dict[bytes, bytes] = {}
    shape = [b""] * n
    for i in range(n - 1, -1, -1):
        node = nodes[i]
        tb = node.type.encode()
        if node.child_count == 0:
            shape[i] = _leaf(tb, leaf_cache)
        else:
            parts = bytearray(tb)
            pos = i + 1
            while pos < end[i]:
                parts += shape[pos]
                pos = end[pos]
            shape[i] = _internal(tb, bytes(parts))

    type_counts = Counter(node.type for node in nodes)
    doc_prefixes = tuple(p.encode() for p in cfg.get("doc_prefixes", ["/**"]))
    comment_bytes = 0
    jsdoc = 0
    for node in nodes:
        if node.type in cfg["comments"]:
            text = node.text
            comment_bytes += len(text)
            if cfg["docstring"] != "python" and text.startswith(doc_prefixes):
                jsdoc += 1

    defs: list[dict] = []
    n_functions = n_classes = n_methods = 0
    docstrings = 0
    for i, node in enumerate(nodes):
        t = node.type
        if t not in cfg["functions"] and t not in cfg["classes"]:
            continue
        # tree-sitter-javascript also emits the anonymous "class" keyword token
        if not node.is_named:
            continue
        is_class = t in cfg["classes"]
        # C/C++ specifiers also serve forward declarations and type uses ("struct s *p")
        if is_class and cfg.get("class_requires_body") and node.child_by_field_name("body") is None:
            continue
        if is_class:
            n_classes += 1
            kind = "class"
        else:
            # inside a class?
            p = parent[i]
            method = False
            while p >= 0:
                if nodes[p].type in cfg.get("method_containers", cfg["classes"]):
                    method = True
                    break
                p = parent[p]
            if not method and cfg.get("qualified_methods") and _qualified_name(node):
                method = True  # out-of-class member definition, e.g. "T C::m() {...}"
            if method or t in cfg["methods"]:
                n_methods += 1
                kind = "method"
            else:
                n_functions += 1
                kind = "function"
        has_doc = False
        if cfg["docstring"] == "python":
            has_doc = _python_docstring(node)
        if has_doc:
            docstrings += 1
        defs.append({
            "kind": kind,
            "start": node.start_byte,
            "end": node.end_byte,
            "nodes": end[i] - i,
            "shape": shape[i].hex(),
            "has_docstring": has_doc,
        })

    has_docstring = docstrings > 0 or (cfg["docstring"] != "python" and jsdoc > 0)
    total_depth = sum(depth)
    feats = {
        "language": language,
        "bytes": len(src),
        "lines": src.count(b"\n") + 1,
        "parse_error": bool(root.has_error),
        "n_nodes": n,
        "n_leaves": sum(1 for x in nodes if x.child_count == 0),
        "n_functions": n_functions,
        "n_methods": n_methods,
        "n_classes": n_classes,
        "n_comments": sum(type_counts.get(t, 0) for t in cfg["comments"]),
        "comment_bytes": comment_bytes,
        "comment_ratio": comment_bytes / max(len(src), 1),
        "max_depth": max(depth) if n else 0,
        "mean_depth": total_depth / max(n, 1),
        "n_imports": sum(type_counts.get(t, 0) for t in cfg["imports"]),
        "n_strings": sum(type_counts.get(t, 0) for t in cfg["strings"]),
        "n_identifiers": sum(type_counts.get(t, 0) for t in cfg["identifiers"]),
        "has_docstring": has_docstring,
        "docstring_count": docstrings if cfg["docstring"] == "python" else jsdoc,
        "shape_root": shape[0].hex() if n else "",
        "n_defs": len(defs),
        "n_def_shapes": len({d["shape"] for d in defs}),
    }
    return feats, defs
