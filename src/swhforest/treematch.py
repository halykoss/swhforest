"""Small structural primitives on raw tree-sitter trees (no DAG)."""

from __future__ import annotations

from .query import Pattern


def node_match(node, pat: Pattern) -> bool:
    if pat.type is not None and node.type != pat.type:
        return False
    if pat.text is not None:
        if node.child_count != 0 or node.text != pat.text:
            return False
    for fname, sub in pat.fields.items():
        ok = False
        for i, c in enumerate(node.children):
            if node.field_name_for_child(i) == fname and node_match(c, sub):
                ok = True
                break
        if not ok:
            return False
    if pat.contains is not None and not desc_match(node, pat.contains):
        return False
    return True


def desc_match(node, pat: Pattern) -> bool:
    # iterative: some real files nest deeper than the Python recursion limit
    stack = [node]
    while stack:
        n = stack.pop()
        if node_match(n, pat):
            return True
        stack.extend(n.children)
    return False


def find_in_tree(root, pat: Pattern):
    out = []
    stack = [root]
    while stack:
        n = stack.pop()
        if node_match(n, pat):
            out.append((n.start_byte, n.end_byte))
        stack.extend(n.children)
    return out


def literal_texts(pat: Pattern) -> list[bytes]:
    out = []
    if pat.text is not None:
        out.append(pat.text)
    for sub in pat.fields.values():
        out.extend(literal_texts(sub))
    if pat.contains is not None:
        out.extend(literal_texts(pat.contains))
    return out


def literal_leaves(pat: Pattern) -> list[tuple[bytes | None, bytes]]:
    """(node type, text) of every literal leaf in the pattern."""
    out = []
    if pat.text is not None:
        out.append((pat.type.encode() if pat.type else None, pat.text))
    for sub in pat.fields.values():
        out.extend(literal_leaves(sub))
    if pat.contains is not None:
        out.extend(literal_leaves(pat.contains))
    return out
