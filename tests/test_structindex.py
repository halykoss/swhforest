"""Tests for swhforest.structindex (the structural index, catalog/structure.parquet).

Run:  PYTHONPATH=src python -m pytest tests/test_structindex.py -q

(a) soundness, property-based: random patterns built from real nodes of real
    snippets (Python and JavaScript, including files with syntax errors); whenever
    treematch.find_in_tree matches, pattern_features(p) must be a subset of
    blob_features(root);
(b) regression: an anonymous node with children (Python ``is not`` / ``not in``
    after error recovery) must not hide a match from the index;
(c) is_exact: for exact patterns, a blob has the pattern's feature iff the
    matcher finds a match;
(d) StructIndex round trip: from_pairs -> save -> load keeps postings and candidates.

Snippets are inline, so the test needs no network.  If blobs of the 100k build
are cached in data/blobs (and catalog/catalog.parquet is present), up to
SWHFOREST_TEST_BLOBS (default 12) of them per language are added to the corpus.
SWHFOREST_FUZZ_SEED changes the random seed (default 20261028).
"""

from __future__ import annotations

import gzip
import os
import random

import numpy as np
import pytest

from swhforest.dag import make_parser
from swhforest.queries import QUERIES
from swhforest.query import Pattern as P
from swhforest.structindex import (FAMILIES, StructIndex, blob_features, feature_hash,
                                   is_exact, named_only_types, pattern_features)
from swhforest.treematch import find_in_tree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = int(os.environ.get("SWHFOREST_FUZZ_SEED", "20261028"))

# ---------------------------------------------------------------------------- corpus
PY = [
    # ordinary code
    '''import json, os


class Model(object):
    """A tiny model."""

    def __init__(self, lr=0.1, *args, **kw):
        self.lr = lr
        self.w = [0.0 for _ in range(10)]
        self.cfg = {k: v for k, v in kw.items() if v is not None}

    @property
    def rate(self):
        return self.lr

    def fit(self, data):
        for x, y in data:
            for i in range(len(self.w)):
                self.w[i] += self.lr * (y - x[i])
            print("step", x, y)
        return self


def train(model, data):
    def loss(a, b):
        return (a - b) ** 2
    model.fit(data)
    with open(os.path.join("out", "w.json")) as f:
        prev = json.load(f)
    return loss(1, 2), prev
''',
    # control flow, exceptions, async, comprehension, lambda, f-strings, operators
    '''async def worker(queue, *, timeout=None):
    results = []
    while True:
        try:
            item = await queue.get()
        except asyncio.CancelledError:
            pass
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"bad {item!r}: {e}") from e
        else:
            if item is not None and item not in results:
                results.append(item)
        finally:
            queue.task_done()
    return sorted(results, key=lambda r: -r)


def read(path):
    data = open(path).read()
    x = eval(data) if data else None
    del data
    assert x is not None, "empty"
    return [y for y in x if not y] or {*x}
''',
    # match statement, decorators, global/nonlocal, walrus, slices
    '''import functools

def deco(fn):
    @functools.wraps(fn)
    def inner(*a, **k):
        nonlocal_counter = 0
        def deeper():
            nonlocal nonlocal_counter
            nonlocal_counter += 1
        deeper()
        return fn(*a, **k)
    return inner

@deco
def handle(cmd):
    global STATE
    match cmd.split():
        case ["go", direction]:
            return direction[::-1]
        case [x, *rest] if (n := len(rest)) > 2:
            return x, n
        case _:
            return None
''',
    # regression case (anonymous node with children after recovery)
    "def train():\n    a is * await open(p) not c\n",
    # syntax errors
    '''def broken(x:
    return open(x
class C:
    def m(self) -> None
        self.a = [1, 2,
    for i in range(3):
        print(i
''',
    "def f():\n    if a not in b is not c:\n        open(f)\n  pass\nexcept:\n    pass\n",
    "x = (1 +\nprint(\"hi\")\ndef g(): return open('a'), json.load(f)\nlambda: yield\n",
]

JS = [
    '''import { api } from "./api.js";

export class Store extends Base {
  #items = [];
  constructor(name) {
    super();
    this.name = name;
  }
  get size() { return this.#items.length; }
  async load(url) {
    for (const u of [url, url + "?2"]) {
      const res = await fetch(u);
      this.#items.push(...(await res.json()));
    }
    return this;
  }
}

function getData(id) {
  return fetch(`/api/${id}`, { method: "GET" })
    .then(r => r.json())
    .catch(err => console.error(err));
}
''',
    '''document.addEventListener("DOMContentLoaded", () => {
  const btn = document.querySelector("#go");
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    let n = 0;
    for (let i = 0; i < 10; i++) {
      if (i % 2 === 0 && !(n > 3)) n += i; else continue;
    }
    const re = /a+b/gi;
    console.log(re.test("aab") ? "yes" : "no", typeof n, n ?? 0);
  });
});

var f = function named(a, b = 2, ...rest) { return a?.b?.[0] ?? rest; };
async function main() {
  for (let i = 0; i < 3; i++) { await sleep(i); }
  try { JSON.parse("{"); } catch { } finally { done(); }
}
''',
    '''const App = () => (
  <div className="app">
    <h1>{title}</h1>
    {items.map(i => <Item key={i.id} {...i} />)}
  </div>
);
switch (x) { case 1: y = 2; break; default: y = new Foo(1, 2); }
label: while (true) { do { x--; } while (x > 0); break label; }
''',
    # syntax errors
    '''function broken(a, {
  return fetch(a).then(r => r.json(;
class { method() { this.x = ; } }
for (let i = 0; i < ; i++) { await go(i) }
''',
    "if (a) { b(); } else { c(\nconst = 3;\nx.then(y).catch(\n",
]


def _cached_blobs():
    """Up to N cached blobs of the 100k build per language, if available locally."""
    n = int(os.environ.get("SWHFOREST_TEST_BLOBS", "12"))
    cat, cache = os.path.join(ROOT, "catalog", "catalog.parquet"), os.path.join(ROOT, "data", "blobs")
    if n <= 0 or not (os.path.exists(cat) and os.path.isdir(cache)):
        return []
    import pyarrow.parquet as pq
    t = pq.read_table(cat, columns=["blob_id", "language", "bytes"])
    out, seen = [], {"python": 0, "javascript": 0}
    for bid, lang, size in sorted(zip(*(t[c].to_pylist() for c in t.column_names))):
        if size > 12_000 or seen.get(lang, n) >= n:
            continue
        p = os.path.join(cache, bid + ".gz")
        if os.path.exists(p):
            with open(p, "rb") as f:
                out.append((lang, gzip.decompress(f.read())))
            seen[lang] += 1
    return out


@pytest.fixture(scope="module")
def corpus():
    """[(language, source bytes, tree, blob features)]"""
    parsers = {"python": make_parser("python"), "javascript": make_parser("javascript")}
    src = [("python", s.encode()) for s in PY] + [("javascript", s.encode()) for s in JS]
    src += _cached_blobs()
    out = []
    for lang, b in src:
        tree = parsers[lang].parse(b)
        out.append((lang, b, tree, blob_features(tree.root_node)))
    # the corpus must contain broken code and anonymous nodes with children
    assert any(t.root_node.has_error for l, _, t, _ in out if l == "python")
    assert any(t.root_node.has_error for l, _, t, _ in out if l == "javascript")
    return out


def _nodes(root):
    out, stack = [], [root]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(n.children)
    return out


def _descendants_or_self(n):
    return _nodes(n)


# ---------------------------------------------------------------------------- generator
def gen_pattern(n, rng: random.Random, depth: int = 0) -> P:
    """A random pattern that node ``n`` matches (under treematch.node_match).

    Each constraint is taken from ``n`` itself, then randomly dropped: the type,
    the leaf text, up to two field children (recursively) and one descendant-or-
    self for ``contains`` (recursively).
    """
    typ = n.type if rng.random() < 0.85 else None
    text = n.text if n.child_count == 0 and rng.random() < 0.5 else None
    fields = {}
    if depth < 4 and n.child_count:
        fc = [(n.field_name_for_child(i), c) for i, c in enumerate(n.children)]
        fc = [(f, c) for f, c in fc if f]
        rng.shuffle(fc)
        for f, c in fc[:rng.choice((0, 1, 1, 2))]:
            if f not in fields:
                fields[f] = gen_pattern(c, rng, depth + 1)
    contains = None
    if depth < 4 and rng.random() < 0.45:
        contains = gen_pattern(rng.choice(_descendants_or_self(n)), rng, depth + 1)
    return P(type=typ, text=text, fields=fields, contains=contains)


# ---------------------------------------------------------------------------- (a)
def test_soundness_random_patterns(corpus):
    rng = random.Random(SEED)
    checked = matched = anon_roots = 0
    by_lang = {}
    for i, (lang, *_rest) in enumerate(corpus):
        by_lang.setdefault(lang, []).append(i)
    for i, (lang, _, tree, _) in enumerate(corpus):
        nodes = _nodes(tree.root_node)
        for _ in range(600):
            n = rng.choice(nodes)
            anon_roots += not n.is_named
            p = gen_pattern(n, rng)
            pf = pattern_features(p, lang)
            # the source tree, plus two other trees of the same language
            others = rng.sample(by_lang[lang], min(2, len(by_lang[lang])))
            for j in {i, *others}:
                _, _, t, bf = corpus[j]
                checked += 1
                if find_in_tree(t.root_node, p):
                    matched += 1
                    missing = pf - bf
                    assert not missing, (f"unsound: {p!r} matches blob {j} ({lang}) "
                                         f"but its features {missing} are not in the blob")
    assert matched >= len(corpus) * 600  # every generated pattern matches its source node
    assert anon_roots > 0
    print(f"\n  soundness: {checked:,} (pattern, blob) pairs, {matched:,} matches, "
          f"{len(corpus)} blobs")


def test_soundness_builtin_queries(corpus):
    for name, (lang, p, _) in QUERIES.items():
        pf = pattern_features(p, lang)
        assert pf, name
        for l, _, t, bf in corpus:
            if l == lang and find_in_tree(t.root_node, p):
                assert pf <= bf, (name, pf - bf)


# ---------------------------------------------------------------------------- (b)
def test_regression_anonymous_node_with_children():
    src = b"def train():\n    a is * await open(p) not c\n"
    lang, pat, _ = QUERIES["py_open"]
    tree = make_parser("python").parse(src)
    # the call to open sits below an anonymous node that has children
    assert any(not n.is_named and n.child_count for n in _nodes(tree.root_node))
    assert find_in_tree(tree.root_node, pat), "the matcher must find the open() call"
    bf = blob_features(tree.root_node)
    assert pattern_features(pat, lang) <= bf
    # and the index built from this blob returns it as a candidate
    hs = np.array([feature_hash(f) for f in bf], dtype=np.uint64)
    fs = np.array([FAMILIES.index(f[0]) for f in bf], dtype=np.uint8)
    idx = StructIndex.from_pairs(hs, fs, np.zeros(hs.size, dtype=np.uint32),
                                 ["python"], np.zeros(1, dtype=np.uint8))
    assert idx.candidates(pat, "python").tolist() == [0]


# ---------------------------------------------------------------------------- (c)
def _exact_patterns(corpus, rng, per_blob=60):
    """(language, pattern) for exact patterns taken from real nodes, plus the built-in queries."""
    out = [(l, p) for _, (l, p, _) in QUERIES.items() if is_exact(p, l)]
    for lang, _, tree, _ in corpus:
        ok = named_only_types(lang)
        cand = []
        for n in _nodes(tree.root_node):
            if not n.is_named or n.type not in ok:
                continue
            for d in _nodes(n)[1:]:
                if d.is_named and d.type in ok and d.type != n.type:
                    cand.append(P(type=n.type, contains=P(type=d.type)))
            for i, c in enumerate(n.children):
                f = n.field_name_for_child(i)
                if f and c.is_named and c.child_count == 0 and c.type in ok:
                    cand.append(P(type=n.type, fields={f: P(type=c.type, text=c.text)}))
        rng.shuffle(cand)
        out += [(lang, p) for p in cand[:per_blob]]
    return out


def test_is_exact(corpus):
    rng = random.Random(SEED + 1)
    pats = _exact_patterns(corpus, rng)
    assert len(pats) > 200
    pos = neg = 0
    for lang, p in pats:
        assert is_exact(p, lang), p
        pf = pattern_features(p, lang)
        # the deciding feature: T for "X.field is the literal t" (which also implies
        # the A pair (X, leaf type)), A for "X contains Y"
        dec = [f for f in pf if f[0] == "T"] or [f for f in pf if f[0] == "A"]
        assert len(dec) == 1 and len(pf) <= 2, (p, pf)
        (f,) = dec
        for l, _, t, bf in corpus:
            if l != lang:
                continue
            m = bool(find_in_tree(t.root_node, p))
            assert m == (f in bf), (p, f, m)
            pos += m
            neg += not m
    assert pos and neg
    # patterns with more than one relation, or with an untyped / ambiguous node, are not exact
    assert not is_exact(QUERIES["py_open"][1], "python")
    assert not is_exact(QUERIES["nested_fn"][1], "python")
    assert not is_exact(P(type="function_definition", contains=P(type="function_definition")), "python")
    assert not is_exact(P(contains=P(type="call")), "python")
    assert not is_exact(P(type="class_declaration", contains=P(type="class")), "javascript")


# ---------------------------------------------------------------------------- (d)
def _index(corpus):
    languages = sorted({l for l, *_ in corpus})
    H, F, R = [], [], []
    for row, (_, _, _, bf) in enumerate(corpus):
        bf = sorted(bf, key=repr)
        H.append(np.array([feature_hash(f) for f in bf], dtype=np.uint64))
        F.append(np.array([FAMILIES.index(f[0]) for f in bf], dtype=np.uint8))
        R.append(np.full(len(bf), row, dtype=np.uint32))
    blob_lang = np.array([languages.index(l) for l, *_ in corpus], dtype=np.uint8)
    # duplicates are allowed in the input pairs
    H.append(H[0][:5]); F.append(F[0][:5]); R.append(R[0][:5])
    return StructIndex.from_pairs(np.concatenate(H), np.concatenate(F), np.concatenate(R),
                                  languages, blob_lang)


def test_roundtrip(corpus, tmp_path):
    idx = _index(corpus)
    assert np.all(np.diff(idx.features.astype(np.uint64)) > 0)  # sorted, unique
    assert int(idx.df.sum()) == idx.blobs.size == int(idx.offsets[-1])
    # postings agree with the extracted features
    for row, (_, _, _, bf) in enumerate(corpus[:5]):
        for f in list(bf)[:50]:
            assert row in idx.postings(feature_hash(f)).tolist()
    path = tmp_path / "structure.parquet"
    idx.save(str(path))

    import pyarrow.parquet as pq
    schema = pq.read_schema(str(path))
    assert [f.name for f in schema] == ["feature", "family", "df", "blobs"]
    assert str(schema.field("feature").type) == "uint64"
    assert str(schema.field("df").type) == "uint32"
    assert str(schema.field("blobs").type) == "list<element: uint32>"
    assert schema.metadata[b"encoding"] == b"gap"

    back = StructIndex.load(str(path))
    assert np.array_equal(back.features, idx.features)
    assert np.array_equal(back.families, idx.families)
    assert np.array_equal(back.df, idx.df)
    assert np.array_equal(back.offsets, idx.offsets)
    assert np.array_equal(back.blobs, idx.blobs)
    assert back.languages == idx.languages
    assert np.array_equal(back.blob_lang, idx.blob_lang)

    rng = random.Random(SEED + 2)
    pats = [(l, p) for _, (l, p, _) in QUERIES.items()]
    for lang, _, tree, _ in corpus[:6]:
        for _ in range(30):
            pats.append((lang, gen_pattern(rng.choice(_nodes(tree.root_node)), rng)))
    absent = P(type="call", fields={"function": P(type="identifier", text="no_such_name_xyz")})
    pats.append(("python", absent))
    for lang, p in pats:
        a, b = idx.candidates(p, lang), back.candidates(p, lang)
        assert (a is None) == (b is None)
        if a is None:
            continue
        assert np.array_equal(a, b), p
        # candidates are sound on this corpus, and of the pattern's language only
        cs = set(a.tolist())
        for row, (l, _, t, _) in enumerate(corpus):
            if l == lang and find_in_tree(t.root_node, p):
                assert row in cs, (p, row)
            if row in cs:
                assert l == lang
    assert back.candidates(absent, "python").size == 0
