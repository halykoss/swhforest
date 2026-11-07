"""Structural index: sound candidate pruning for tree patterns over a whole build.

Every blob contributes three families of features, extracted from its tree-sitter
tree (features name named nodes only):

  E  (parent type, field, child type)            labelled parent-child edge
  T  (parent type, field, leaf type, leaf text)  literal leaf in its syntactic context
  A  (ancestor type, descendant type)            strict ancestor-descendant type pair

A pattern is compiled into the features that every match must contain.  The
index intersects their postings (rarest first) and returns candidate blobs; the
caller parses only those and runs the exact matcher.  For patterns that consist
of a single relation (``X`` contains ``Y``, or ``X.field`` is the literal ``t``)
the feature is also sufficient, so the candidates are the answer and no blob
needs to be fetched (``is_exact``).  Pruning is sound with
respect to that matcher: a feature is emitted only if every match implies it,
and only for node types that are named-only in the grammar (a type such as
JavaScript ``class`` exists both as a named node and as an anonymous keyword,
and the matcher compares type strings, so it could match a node the index does
not record).  Features are stored as 64-bit hashes; a hash collision merges two
posting lists, which only adds candidates.

On disk the index is one Parquet file, ``structure.parquet``:
  feature  uint64   hash of the feature tuple
  family   string   "E", "T" or "A"
  df       uint32   number of blobs containing the feature
  blobs    list<uint32>  row numbers in catalog.parquet, gap-encoded (first value,
                         then differences), so the lists compress well
"""

from __future__ import annotations

import hashlib

import numpy as np

from .query import Pattern

FAMILIES = ("E", "T", "A")


def feature_hash(feat: tuple) -> int:
    return int.from_bytes(hashlib.blake2b(repr(feat).encode(), digest_size=8).digest(), "little")


# ------------------------------------------------------------------ grammar helpers
def _language(grammar: str):
    from .grammars import get_language
    return get_language(grammar)


_NAMED_ONLY: dict[str, frozenset] = {}


def named_only_types(lang: str) -> frozenset:
    """Node types that only ever occur as named nodes in every grammar of ``lang``."""
    if lang not in _NAMED_ONLY:
        from .grammars import LANGUAGE_GRAMMARS
        named, anon = set(), set()
        for g in LANGUAGE_GRAMMARS.get(lang, (lang,)):
            L = _language(g)
            for i in range(L.node_kind_count):
                k = L.node_kind_for_id(i)
                if k is None:
                    continue
                (named if L.node_kind_is_named(i) else anon).add(k)
        _NAMED_ONLY[lang] = frozenset(named - anon)
    return _NAMED_ONLY[lang]


# ------------------------------------------------------------------ extraction
def blob_features(root) -> set[tuple]:
    """All E, T, A features of one tree.

    The walk visits every node, as the matcher does: some anonymous nodes have
    children (Python's ``is not`` and ``not in``), and after error recovery a
    whole named subtree can sit below one of them.  Features name only named
    nodes, and anonymous nodes are not recorded as ancestors.
    """
    out: set[tuple] = set()
    stack = [(root, ())]
    while stack:
        n, anc = stack.pop()
        named = n.is_named
        t = n.type
        if named:
            for a in anc:
                out.add(("A", a, t))
        if n.child_count == 0:
            continue
        anc2 = anc if (not named or t in anc) else anc + (t,)
        for i, c in enumerate(n.children):
            if named and c.is_named:
                f = n.field_name_for_child(i)
                if f:
                    if c.child_count == 0:
                        out.add(("T", t, f, c.type, c.text.decode("utf-8", "replace")))
                    else:
                        out.add(("E", t, f, c.type))
            if c.is_named or c.child_count:
                stack.append((c, anc2))
    return out


def pattern_features(pat: Pattern, lang: str) -> set[tuple]:
    """Features that every match of ``pat`` (under treematch.node_match) contains."""
    ok = named_only_types(lang)
    out: set[tuple] = set()

    def typed(p):
        return p.type is not None and p.type in ok

    def nonleaf(p):
        """Every node matching p has children."""
        if p.fields:
            return True
        c = p.contains
        return c is not None and typed(p) and typed(c) and c.type != p.type

    def go(p) -> set[str]:
        below: set[str] = set()  # named-only types that must occur strictly below p
        for fname, sub in p.fields.items():
            sb = go(sub)
            if typed(p) and typed(sub):
                if sub.text is not None and not sub.fields and sub.contains is None:
                    out.add(("T", p.type, fname, sub.type, sub.text.decode()))
                elif nonleaf(sub):
                    out.add(("E", p.type, fname, sub.type))
            if typed(sub):
                below.add(sub.type)
            below |= sb
        if p.contains is not None:
            c = p.contains
            # c matches p's root or a descendant; what lies strictly below c lies below p
            below |= go(c)
            if typed(p) and typed(c) and c.type != p.type:
                below.add(c.type)
        if typed(p):
            for t in below:
                out.add(("A", p.type, t))
        return below

    go(pat)
    return out


def is_exact(pat: Pattern, lang: str) -> bool:
    """True if a blob contains a match iff it has the pattern's single feature.

    Two shapes qualify: ``X`` that contains ``Y`` (an A feature, X != Y) and
    ``X`` whose field ``f`` is the leaf ``Y`` with text ``t`` (a T feature).
    Both types must be named-only.  Exact up to 64-bit hash collisions.
    """
    ok = named_only_types(lang)
    if pat.type not in ok or pat.text is not None:
        return False
    if pat.contains is not None and not pat.fields:
        c = pat.contains
        return (c.type in ok and c.type != pat.type and c.text is None
                and not c.fields and c.contains is None)
    if pat.contains is None and len(pat.fields) == 1:
        (sub,) = pat.fields.values()
        return (sub.type in ok and sub.text is not None
                and not sub.fields and sub.contains is None)
    return False


# ------------------------------------------------------------------ index
class StructIndex:
    def __init__(self, features, families, df, offsets, blobs, languages, blob_lang):
        self.features = features      # sorted uint64
        self.families = families      # uint8 index into FAMILIES
        self.df = df                  # uint32
        self.offsets = offsets        # uint64, len = n_features + 1
        self.blobs = blobs            # uint32 postings, concatenated
        self.languages = languages    # list[str]
        self.blob_lang = blob_lang    # uint8 per catalog row
        self.last_plan: list[dict] = []
        self.last_exact = False

    # -- build ---------------------------------------------------------------
    @classmethod
    def from_pairs(cls, hashes: np.ndarray, fams: np.ndarray, rows: np.ndarray,
                   languages: list[str], blob_lang: np.ndarray) -> "StructIndex":
        """``hashes[i]`` occurs in catalog row ``rows[i]`` (duplicates allowed)."""
        order = np.lexsort((rows, hashes))
        h, r, f = hashes[order], rows[order], fams[order]
        keep = np.ones(h.size, dtype=bool)
        keep[1:] = (h[1:] != h[:-1]) | (r[1:] != r[:-1])
        h, r, f = h[keep], r[keep], f[keep]
        starts = np.flatnonzero(np.r_[True, h[1:] != h[:-1]])
        offsets = np.r_[starts, h.size].astype(np.uint64)
        return cls(h[starts], f[starts].astype(np.uint8), np.diff(offsets).astype(np.uint32),
                   offsets, r.astype(np.uint32), languages, blob_lang.astype(np.uint8))

    def save(self, path: str) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq
        v = self.blobs.astype(np.int64)
        gaps = np.diff(v, prepend=0)
        starts = self.offsets[:-1].astype(np.int64)
        gaps[starts[self.df > 0]] = v[starts[self.df > 0]]
        blobs = pa.ListArray.from_arrays(pa.array(self.offsets.astype(np.int64)), pa.array(gaps.astype(np.uint32)))
        t = pa.table({"feature": pa.array(self.features), "family": pa.array([FAMILIES[i] for i in self.families]),
                      "df": pa.array(self.df), "blobs": blobs})
        meta = {b"languages": ",".join(self.languages).encode(), b"blob_lang": self.blob_lang.tobytes(),
                b"encoding": b"gap"}
        pq.write_table(t.replace_schema_metadata(meta), path, compression="zstd", compression_level=12,
                       use_dictionary=["family"])

    @classmethod
    def load(cls, path: str) -> "StructIndex":
        import pyarrow.parquet as pq
        t = pq.read_table(path)
        md = t.schema.metadata
        b = t.column("blobs").combine_chunks()
        offsets = b.offsets.to_numpy().astype(np.int64)
        vals = b.values.to_numpy()
        if md.get(b"encoding") == b"gap":
            c = np.cumsum(vals, dtype=np.int64)
            base = np.r_[0, c][offsets[:-1]]
            vals = (c - np.repeat(base, np.diff(offsets))).astype(np.uint32)
        fam = {k: i for i, k in enumerate(FAMILIES)}
        return cls(t.column("feature").to_numpy(),
                   np.array([fam[x] for x in t.column("family").to_pylist()], dtype=np.uint8),
                   t.column("df").to_numpy(), offsets.astype(np.uint64), vals,
                   md[b"languages"].decode().split(","),
                   np.frombuffer(md[b"blob_lang"], dtype=np.uint8).copy())

    # -- query ---------------------------------------------------------------
    def postings(self, h: int) -> np.ndarray | None:
        i = int(np.searchsorted(self.features, np.uint64(h)))
        if i == self.features.size or int(self.features[i]) != h:
            return None
        return self.blobs[int(self.offsets[i]):int(self.offsets[i + 1])]

    def lang_rows(self, language: str) -> np.ndarray:
        return np.flatnonzero(self.blob_lang == self.languages.index(language))

    def candidates(self, pat: Pattern, language: str) -> np.ndarray | None:
        """Catalog rows that can contain a match, or None if the pattern implies no feature."""
        feats = pattern_features(pat, language)
        self.last_plan = []
        self.last_exact = is_exact(pat, language)
        if not feats:
            return None
        lists = []
        for f in feats:
            p = self.postings(feature_hash(f))
            if p is None:  # feature absent from the build: no blob can match
                self.last_plan = [{"feature": f, "df": 0}]
                return np.empty(0, dtype=np.uint32)
            lists.append((p.size, f, p))
        lists.sort(key=lambda x: x[0])  # rarest first
        out = lists[0][2]
        self.last_plan.append({"feature": lists[0][1], "df": lists[0][0], "after": int(out.size)})
        for n, f, p in lists[1:]:
            if out.size == 0:
                break
            out = np.intersect1d(out, p, assume_unique=True)
            self.last_plan.append({"feature": f, "df": n, "after": int(out.size)})
        lid = self.languages.index(language)
        return out[self.blob_lang[out] == lid]


class ShardedIndex:
    """Several structure-<k>.parquet shards of one language with disjoint row ranges."""

    def __init__(self, shards: list[StructIndex]):
        self.shards = shards
        self.last_exact = False

    @classmethod
    def load(cls, directory: str) -> "ShardedIndex":
        import glob
        return cls([StructIndex.load(p) for p in sorted(glob.glob(f"{directory}/structure-*.parquet"))])

    def candidates(self, pat: Pattern, language: str) -> np.ndarray | None:
        parts = []
        for s in self.shards:
            c = s.candidates(pat, language)
            self.last_exact = s.last_exact
            if c is None:
                return None
            parts.append(c)
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.uint32)
