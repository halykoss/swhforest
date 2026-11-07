"""Merkle DAG over tree-sitter concrete syntax trees.

The identity of a node is computed bottom-up, exactly like a Merkle hash:

* a leaf is ``H("L", type, text)``;
* an internal node is ``H("I", type, (field, child_id)*)``.

Two nodes with the same digest have the same type, the same text (leaves) or
the same ordered children in the same grammar fields (internal nodes).  The
digest is interned in a hash-consing table, so each distinct subtree exists
exactly once in the DAG, no matter how many blobs or how many positions it
occurs in.

The DAG carries *shape*, not *position*.  It is used to evaluate patterns
(``swhforest.query``) and provides ``make_parser``.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from array import array
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from tree_sitter import Language, Parser

KIND_LEAF = 0
KIND_INTERNAL = 1
SENTINEL = 0xFFFFFFFF

# 128-bit digests: collision probability for a few million nodes is ~1e-23.
_DIGEST_SIZE = 16


def _h(*parts: bytes) -> bytes:
    h = hashlib.blake2b(digest_size=_DIGEST_SIZE, person=b"swhforest-dag")
    for p in parts:
        h.update(p)
    return h.digest()


def make_parser(language: str = "python", path: str | None = None) -> Parser:
    from .grammars import make_parser as _mk
    return _mk(language, path)


class Dag:
    """Hash-consed DAG of tree-sitter nodes.

    Node table (all arrays are indexed by node id, children always have a
    smaller id than their parent because of bottom-up interning):

    ``kind``       one byte per node, KIND_LEAF / KIND_INTERNAL
    ``type_of``    type id (index into ``types``)
    ``leaf_text``  text id for leaves, SENTINEL for internal nodes
    ``ptr``        CSR offsets into ``child`` / ``field`` (len = n_nodes + 1)
    ``child``      child node ids, in grammar order
    ``field``      field name id for the corresponding edge (0 = no field)

    ``subtree_size`` is derived: it only depends on the node's own identity,
    so it is shared by every occurrence.
    """

    def __init__(self) -> None:
        self.types: list[bytes] = []
        self._type_ids: dict[bytes, int] = {}
        self.field_names: list[bytes] = [b""]
        self._field_ids: dict[bytes, int] = {b"": 0}

        self.kind = bytearray()
        self.type_of = array("I")
        self.leaf_text = array("I")

        self.texts: list[bytes] = []
        self._text_ids: dict[bytes, int] = {}

        self.ptr = array("I", [0])
        self.child = array("I")
        self.field = array("H")

        self._intern: dict[bytes, int] = {}

        self._subtree_size: np.ndarray | None = None
        self._leaf_len: np.ndarray | None = None
        self._shapes: np.ndarray | None = None

    # -- interning ---------------------------------------------------------
    def type_id(self, name: bytes) -> int:
        tid = self._type_ids.get(name)
        if tid is None:
            tid = len(self.types)
            self._type_ids[name] = tid
            self.types.append(name)
        return tid

    def field_id(self, name: bytes | None) -> int:
        name = name or b""
        fid = self._field_ids.get(name)
        if fid is None:
            fid = len(self.field_names)
            self._field_ids[name] = fid
            self.field_names.append(name)
        return fid

    def _text_id(self, text: bytes) -> int:
        tid = self._text_ids.get(text)
        if tid is None:
            tid = len(self.texts)
            self._text_ids[text] = tid
            self.texts.append(text)
        return tid

    def _new_node(self, kind: int, type_id: int, text_id: int) -> int:
        nid = len(self.kind)
        self.kind.append(kind)
        self.type_of.append(type_id)
        self.leaf_text.append(text_id)
        return nid

    def intern_leaf(self, type_id: int, text: bytes) -> int:
        key = _h(b"L", struct.pack("<I", type_id), text)
        nid = self._intern.get(key)
        if nid is None:
            nid = self._new_node(KIND_LEAF, type_id, self._text_id(text))
            self.ptr.append(len(self.child))
            self._intern[key] = nid
        return nid

    def intern_internal(self, type_id: int, pairs: Iterable[tuple[int, int]]) -> int:
        pairs = list(pairs)
        payload = bytearray(struct.pack("<I", type_id))
        for fid, cid in pairs:
            payload += struct.pack("<HI", fid, cid)
        key = _h(b"I", bytes(payload))
        nid = self._intern.get(key)
        if nid is None:
            nid = self._new_node(KIND_INTERNAL, type_id, SENTINEL)
            for fid, cid in pairs:
                self.child.append(cid)
                self.field.append(fid)
            self.ptr.append(len(self.child))
            self._intern[key] = nid
        return nid

    # -- accessors ---------------------------------------------------------
    @property
    def n_nodes(self) -> int:
        return len(self.kind)

    def children(self, nid: int) -> list[int]:
        return list(self.child[self.ptr[nid]: self.ptr[nid + 1]])

    def children_with_field(self, nid: int, fid: int) -> list[int]:
        lo, hi = self.ptr[nid], self.ptr[nid + 1]
        return [self.child[i] for i in range(lo, hi) if self.field[i] == fid]

    def text_of(self, nid: int) -> bytes:
        return self.texts[self.leaf_text[nid]]

    @property
    def subtree_size(self) -> np.ndarray:
        if self._subtree_size is None:
            # children always have smaller ids, so a single ascending pass works
            ptr = np.asarray(self.ptr, dtype=np.uint32).tolist()
            child = np.asarray(self.child, dtype=np.uint32).tolist()
            size = [1] * self.n_nodes
            for nid in range(self.n_nodes):
                total = 1
                for i in range(ptr[nid], ptr[nid + 1]):
                    total += size[child[i]]
                size[nid] = total
            self._subtree_size = np.array(size, dtype=np.uint32)
        return self._subtree_size

    @property
    def leaf_len(self) -> np.ndarray:
        if self._leaf_len is None:
            out = np.zeros(self.n_nodes, dtype=np.uint32)
            for nid in range(self.n_nodes):
                if self.kind[nid] == KIND_LEAF:
                    out[nid] = len(self.texts[self.leaf_text[nid]])
            self._leaf_len = out
        return self._leaf_len

    # -- parsing -----------------------------------------------------------
    def add_tree(self, src: bytes, parser: Parser) -> "BlobTree":
        """Parse ``src`` and hash-cons it into this DAG.

        Returns the occurrence record: the preorder stream of node ids plus the
        byte offset of every leaf, both needed to put the shared shape back at
        concrete positions in this blob.
        """
        tree = parser.parse(src)
        root = tree.root_node

        # 1. preorder enumeration of the tree-sitter CST, with parent links
        nodes = []
        parent = array("i")
        stack = [(root, -1)]
        while stack:
            node, par = stack.pop()
            idx = len(nodes)
            nodes.append(node)
            parent.append(par)
            # push children in reverse so that they are visited in order
            kids = node.children
            for c in reversed(kids):
                stack.append((c, idx))

        n = len(nodes)
        end = array("i", range(1, n + 1))
        # 2. subtree end positions (preorder subtrees are contiguous)
        for i in range(n - 1, -1, -1):
            p = parent[i]
            if p >= 0 and end[i] > end[p]:
                end[p] = end[i]

        # 3. bottom-up interning, children before parents
        dag_id = array("I", bytes(4 * n))
        leaf_offsets = array("I")
        for i in range(n - 1, -1, -1):
            node = nodes[i]
            tid = self.type_id(node.type.encode())
            if node.child_count == 0:
                dag_id[i] = self.intern_leaf(tid, node.text)
                leaf_offsets.append(node.start_byte)
            else:
                pairs = []
                pos = i + 1
                ci = 0
                while pos < end[i]:
                    fname = node.field_name_for_child(ci)
                    pairs.append((self.field_id(fname.encode() if fname else b""), dag_id[pos]))
                    pos = end[pos]
                    ci += 1
                dag_id[i] = self.intern_internal(tid, pairs)

        # leaves were appended in postorder: reverse to preorder
        leaf_offsets.reverse()
        preorder = np.frombuffer(dag_id, dtype=np.uint32).copy()
        return BlobTree(
            blob_id="",
            size=len(src),
            preorder=preorder,
            leaf_off=np.frombuffer(leaf_offsets, dtype=np.uint32).copy(),
            has_error=bool(root.has_error),
        )

    # -- shapes (for similarity) ------------------------------------------
    def shape_ids(self) -> np.ndarray:
        """Structure-only identity: leaf text is ignored.

        ``evaluate`` and ``score`` in the fixture get the same shape id even
        though their Merkle ids differ, because they differ only in leaves.
        """
        if self._shapes is not None:
            return self._shapes
        out = np.empty(self.n_nodes, dtype=np.uint32)
        table: dict[bytes, int] = {}
        ptr = self.ptr
        child = self.child
        kind = self.kind
        for nid in range(self.n_nodes):
            tid = struct.pack("<I", self.type_of[nid])
            if kind[nid] == KIND_LEAF:
                key = _h(b"sL", tid)
            else:
                payload = bytearray(tid)
                for i in range(ptr[nid], ptr[nid + 1]):
                    payload += struct.pack("<I", out[child[i]])
                key = _h(b"sI", bytes(payload))
            sid = table.get(key)
            if sid is None:
                sid = len(table)
                table[key] = sid
            out[nid] = sid
        self._shapes = out
        return out


@dataclass
class BlobTree:
    blob_id: str
    size: int
    preorder: np.ndarray      # uint32 node ids, preorder
    leaf_off: np.ndarray      # uint32 byte offset of each leaf, preorder
    has_error: bool = False

    @property
    def n_instances(self) -> int:
        return int(self.preorder.size)

    def topology_key(self) -> bytes:
        return _h(b"T", self.preorder.tobytes())
