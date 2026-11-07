"""Queries over the DAG: exact structural patterns and shape similarity.

The matcher never recurses per occurrence.  A pattern is compiled into one
boolean array over the (deduplicated) DAG: node types and leaf texts are
checked directly, field sub-patterns are resolved by looking at the edges, and
``contains`` is a single upward propagation over the DAG.  The cost of a query
is therefore O(nodes of the DAG), independent of how many times each node
occurs in the corpus.
"""

from __future__ import annotations

import itertools
from collections import defaultdict

import numpy as np

from .dag import KIND_LEAF, Dag


class Pattern:
    """A small structural pattern.

    ``type``       node type, e.g. "function_definition"
    ``text``       exact leaf text (leaves only)
    ``fields``     grammar field name -> sub-pattern
    ``contains``   sub-pattern that must match somewhere in the subtree
    """

    _ids = itertools.count()

    def __init__(self, type=None, text=None, fields=None, contains=None):
        self.id = next(Pattern._ids)
        self.type = type
        self.text = text.encode() if isinstance(text, str) else text
        self.fields = fields or {}
        self.contains = contains

    def __repr__(self) -> str:
        bits = []
        if self.type:
            bits.append(self.type)
        if self.text:
            bits.append(repr(self.text.decode(errors="replace")))
        for k, v in self.fields.items():
            bits.append(f"{k}:{v!r}")
        if self.contains is not None:
            bits.append(f"contains[{self.contains!r}]")
        return "(" + " ".join(bits) + ")"


class Matcher:
    def __init__(self, dag: Dag) -> None:
        self.dag = dag
        self.visits = 0
        self._type_id = {t: i for i, t in enumerate(dag.types)}
        self._field_id = {t: i for i, t in enumerate(dag.field_names)}
        self._by_type: dict[int, np.ndarray] = {}
        ptr = np.asarray(dag.ptr, dtype=np.uint32)
        self._ptr = ptr
        self._child = np.asarray(dag.child, dtype=np.uint32)
        self._field = np.asarray(dag.field, dtype=np.uint16)
        self._kind = np.asarray(dag.kind, dtype=np.uint8)
        self._leaf_text = np.asarray(dag.leaf_text, dtype=np.uint32)
        self._type_of = np.asarray(dag.type_of, dtype=np.uint32)
        self._edge_parent = np.repeat(
            np.arange(dag.n_nodes, dtype=np.uint32), np.diff(ptr).astype(np.uint32)
        )

    def nodes_of_type(self, type_name: str) -> np.ndarray:
        tid = self._type_id.get(type_name.encode())
        if tid is None:
            return np.empty(0, dtype=np.uint32)
        if tid not in self._by_type:
            self._by_type[tid] = np.nonzero(self._type_of == tid)[0].astype(np.uint32)
        return self._by_type[tid]

    # -- compilation -------------------------------------------------------
    def search(self, pat: Pattern) -> np.ndarray:
        self.visits = 0
        return self._eval(pat)

    def _base_mask(self, pat: Pattern) -> np.ndarray:
        out = np.ones(self.dag.n_nodes, dtype=bool)
        if pat.type is not None:
            tid = self._type_id.get(pat.type.encode())
            if tid is None:
                return np.zeros(self.dag.n_nodes, dtype=bool)
            out &= self._type_of == tid
        if pat.text is not None:
            text_id = self.dag._text_ids.get(pat.text)
            if text_id is None:
                return np.zeros(self.dag.n_nodes, dtype=bool)
            out &= (self._kind == KIND_LEAF) & (self._leaf_text == text_id)
        return out

    def _eval(self, pat: Pattern) -> np.ndarray:
        self.visits += self.dag.n_nodes
        out = self._base_mask(pat)
        for fname, sub in pat.fields.items():
            fid = self._field_id.get(fname.encode())
            if fid is None:
                return np.zeros(self.dag.n_nodes, dtype=bool)
            sub_mask = self._eval(sub)
            has = np.zeros(self.dag.n_nodes, dtype=bool)
            edges = (self._field == fid) & sub_mask[self._child]
            has[self._edge_parent[edges]] = True
            out &= has
        if pat.contains is not None:
            out &= self._contains(self._eval(pat.contains))
        return out

    def _contains(self, sub: np.ndarray) -> np.ndarray:
        """Node matches if it or any descendant matches ``sub``.

        Upward closure over the DAG: one vectorised step per tree level, so
        the number of steps depends on the nesting depth, not on the corpus
        size.
        """
        res = sub.copy()
        n = self.dag.n_nodes
        for _ in range(128):
            touched = res[self._child]
            if not touched.any():
                break
            prop = np.bincount(
                self._edge_parent[touched], minlength=n
            ) > 0
            new = res | prop
            if int(new.sum()) == int(res.sum()):
                break
            res = new
        return res


# -- canned queries ---------------------------------------------------------
def q_train_fit() -> Pattern:
    """Functions named ``train`` that call ``model.fit(...)``."""
    return Pattern(
        type="function_definition",
        fields={"name": Pattern(type="identifier", text="train")},
        contains=Pattern(
            type="call",
            fields={"function": Pattern(
                type="attribute",
                fields={"attribute": Pattern(type="identifier", text="fit")},
            )},
        ),
    )


def q_fit() -> Pattern:
    """Functions containing any ``x.fit(...)`` call."""
    return Pattern(
        type="function_definition",
        contains=Pattern(
            type="call",
            fields={"function": Pattern(
                type="attribute",
                fields={"attribute": Pattern(type="identifier", text="fit")},
            )},
        ),
    )


def q_execute() -> Pattern:
    """Functions containing a call of the form ``x.execute(...)``."""
    return Pattern(
        type="function_definition",
        contains=Pattern(
            type="call",
            fields={"function": Pattern(
                type="attribute",
                fields={"attribute": Pattern(type="identifier", text="execute")},
            )},
        ),
    )


def q_open() -> Pattern:
    """Functions that call the builtin ``open`` (a plain-name call)."""
    return Pattern(
        type="function_definition",
        contains=Pattern(
            type="call",
            fields={"function": Pattern(type="identifier", text="open")},
        ),
    )


def q_print_in_loop() -> Pattern:
    """Functions with a ``print`` call inside a ``for`` loop."""
    return Pattern(
        type="function_definition",
        contains=Pattern(
            type="for_statement",
            contains=Pattern(
                type="call",
                fields={"function": Pattern(type="identifier", text="print")},
            ),
        ),
    )


def q_nested_function() -> Pattern:
    """Function definitions declared inside another function (any depth)."""
    return Pattern(
        type="function_definition",
        fields={"body": Pattern(type="block",
                                contains=Pattern(type="function_definition"))},
    )


QUERIES = {
    "train-fit": q_train_fit,
    "fit": q_fit,
    "execute": q_execute,
    "open": q_open,
    "print-in-loop": q_print_in_loop,
    "nested-function": q_nested_function,
}


# -- similarity -------------------------------------------------------------
def shape_clusters(dag: Dag, type_name: str = "function_definition", min_size: int = 2):
    """Group nodes by structural identity modulo leaf text (Deckard-style)."""
    shapes = dag.shape_ids()
    tid = {t: i for i, t in enumerate(dag.types)}.get(type_name.encode())
    if tid is None:
        return [], shapes
    nids = np.nonzero(np.asarray(dag.type_of) == tid)[0]
    groups: dict[int, list[int]] = defaultdict(list)
    for nid in nids:
        groups[int(shapes[nid])].append(int(nid))
    clusters = [(sid, ns) for sid, ns in groups.items() if len(ns) >= min_size]
    clusters.sort(key=lambda x: -len(x[1]))
    return clusters, shapes


def type_histogram(dag: Dag, nid: int) -> np.ndarray:
    """Count node types in the expansion of ``nid`` (instances, not DAG nodes)."""
    hist = np.zeros(len(dag.types), dtype=np.int32)
    stack = [nid]
    while stack:
        n = stack.pop()
        hist[dag.type_of[n]] += 1
        stack.extend(dag.children(n))
    return hist


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def nearest_pairs(dag: Dag, nids: list[int], top_k: int = 5):
    """Deckard-style characteristic vectors: cosine over node-type counts."""
    if len(nids) < 2:
        return []
    mat = np.stack([type_histogram(dag, n) for n in nids]).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    mat /= norms[:, None]
    sim = mat @ mat.T
    np.fill_diagonal(sim, -1.0)
    ii, jj = np.triu_indices(len(nids), k=1)
    vals = sim[ii, jj]
    order = np.argsort(-vals)[:top_k]
    return [(float(vals[k]), int(nids[ii[k]]), int(nids[jj[k]])) for k in order]
