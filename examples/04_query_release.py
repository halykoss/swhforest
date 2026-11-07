"""Example 4: structural queries on one language of the release (data/release/<language>/).

The release index of a language is split into shards of 2M catalog rows
(structure-000.parquet, structure-001.parquet, ...); ``ShardedIndex.load(dir)``
loads all of them and ``candidates(pattern, language)`` returns the union of
the per-shard candidates.  Row numbers are global within the language: row r is
the r-th row of ``rows.parquet`` (sorted by blob_id), and ``catalog.parquet``
carries the same number in its ``row`` column (blobs that SWH could not serve
are listed in missing.txt and have no catalog row and no index posting).

For every pattern the script prints the candidate count, whether the candidates
are the exact answer (``is_exact``: no blob needs to be fetched) and whether the
pattern compiles to an ast-grep rule (``swhforest.astgrep.compile_pattern``), so
inexact candidates can be verified by ast-grep instead of the Python matcher.
For python and javascript it uses the built-in queries of ``swhforest.queries``
and compares the candidate counts with the full-scan soundness check recorded in
ingest_metrics.json; for rust it uses a few patterns defined below.  Nothing is
downloaded unless ``--fetch N`` is given (then N candidates of the first inexact
pattern are fetched from the Software Heritage S3 bucket and verified).

Usage:
    PYTHONPATH=src python examples/04_query_release.py [data/release/rust] [--fetch N]

ShardedIndex.load decodes every posting into memory as uint32: rust (one shard,
243,304,888 postings) fits in 8 GB; python (5 shards, 2,828,664,406 postings,
ingest_metrics.json) needs more than 11 GB.

Expected output (data/release/rust --fetch 5; load time varies):
  === Example 4: Structural Queries on the Release (rust) ===
  Loaded 1 shard(s) of data/release/rust: 9,502,255 features, 243,304,888 postings (11.02 s)
  rows.parquet: 448,639 selected blobs; catalog.parquet: 448,639 ingested blobs

    query            candidates  % of blobs  exact  ast-grep
    unsafe_in_fn         29,527       6.58%   True      True
    panic_macro          43,974       9.80%   True      True
    unwrap_call         161,003      35.89%  False      True
    clone_in_loop        54,574      12.16%  False      True

    first candidates of unwrap_call (row, blob_id, repo:path):
      0  0000056d8e7b0907559ad39a145a3f3eb410595c  RReverser/static_aabb2d_index:/examples/index_tree_structure.rs
      2  0000131f5301e45751cce23f379e2ab5fe5bc1f9  alxiong/cryptopal:/xor/src/lib.rs
      4  00001f4f7f9650ee58fb62699fc94857d2f6664b  barabadzhi/promptly:/src/lib.rs
    row/blob_id check against rows.parquet: True
    fetched 5 candidates from SWH: 5 contain a match

unsafe_in_fn and panic_macro are single-relation patterns (is_exact), so their
candidates are the answer; the other two must be fetched and verified.
"""

import gzip
import json
import os
import sys
import time
import urllib.request

import numpy as np
import pyarrow.parquet as pq

from swhforest.astgrep import compile_pattern
from swhforest.grammars import make_parser
from swhforest.queries import QUERIES
from swhforest.query import Pattern
from swhforest.structindex import ShardedIndex, is_exact
from swhforest.treematch import find_in_tree


def _method_call(name):
    """``<expr>.name(...)``: a call whose function is a field expression with this field."""
    return Pattern(type="call_expression", fields={"function": Pattern(
        type="field_expression", fields={"field": Pattern(type="field_identifier", text=name)})})


RUST = {
    "unsafe_in_fn": Pattern(type="function_item", contains=Pattern(type="unsafe_block")),
    "panic_macro": Pattern(type="macro_invocation", fields={"macro": Pattern(type="identifier", text="panic")}),
    "unwrap_call": _method_call("unwrap"),
    "clone_in_loop": Pattern(type="for_expression", contains=_method_call("clone")),
}


def fetch(blob_id):
    url = f"https://softwareheritage.s3.amazonaws.com/content/{blob_id}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return gzip.decompress(r.read())


def main():
    args, n_fetch = list(sys.argv[1:]), 0
    if "--fetch" in args:
        i = args.index("--fetch")
        n_fetch = int(args[i + 1])
        del args[i:i + 2]
    rdir = (args[0] if args else "data/release/rust").rstrip("/")
    lang = os.path.basename(rdir)
    print(f"=== Example 4: Structural Queries on the Release ({lang}) ===")

    t0 = time.perf_counter()
    idx = ShardedIndex.load(rdir)
    if not idx.shards:
        sys.exit(f"no structure-*.parquet in {rdir}")
    feats = sum(s.features.size for s in idx.shards)
    posts = sum(s.blobs.size for s in idx.shards)
    print(f"Loaded {len(idx.shards)} shard(s) of {rdir}: {feats:,} features, {posts:,} postings "
          f"({time.perf_counter() - t0:.2f} s)")
    rows = pq.read_table(f"{rdir}/rows.parquet", columns=["blob_id"])["blob_id"]
    cat = pq.read_table(f"{rdir}/catalog.parquet", columns=["row", "blob_id", "repo", "path"])
    cat_rows = cat["row"].to_numpy()  # sorted by row
    print(f"rows.parquet: {len(rows):,} selected blobs; catalog.parquet: {cat.num_rows:,} ingested blobs")

    if lang == "rust":
        pats = RUST
    else:
        pats = {q: p for q, (l, p, _) in QUERIES.items() if l == lang}
    if not pats:
        sys.exit(f"no example patterns for {lang}")
    recorded = {}
    mpath = f"{rdir}/ingest_metrics.json"
    if os.path.exists(mpath):
        recorded = json.load(open(mpath)).get("soundness") or {}

    print(f"\n  {'query':<16}{'candidates':>11}{'% of blobs':>12}{'exact':>7}{'ast-grep':>10}"
          + ("  recorded (matches/candidates)" if recorded else ""))
    first_inexact = None
    for q, pat in pats.items():
        c = idx.candidates(pat, lang)
        n = cat.num_rows if c is None else int(c.size)
        exact = is_exact(pat, lang)
        line = (f"  {q:<16}{n:>11,}{100 * n / cat.num_rows:>11.2f}%{str(exact):>7}"
                f"{str(compile_pattern(pat, lang) is not None):>10}")
        if q in recorded:
            r = recorded[q]
            line += f"  {r['matching_blobs']:,}/{r['candidates']:,} (same: {r['candidates'] == n})"
        print(line)
        if not exact and c is not None and first_inexact is None:
            first_inexact = (q, pat, c)

    if first_inexact is None:
        return
    q, pat, c = first_inexact
    print(f"\n  first candidates of {q} (row, blob_id, repo:path):")
    pos = np.searchsorted(cat_rows, c[:3].astype(np.int64))
    for r, p in zip(c[:3], pos):
        print(f"    {int(r)}  {cat['blob_id'][int(p)]}  {cat['repo'][int(p)]}:{cat['path'][int(p)]}")
    sample = c[:: max(1, c.size // 1000)][:1000].astype(np.int64)
    ok = all(rows[int(r)].as_py() == cat["blob_id"][int(p)].as_py()
             for r, p in zip(sample, np.searchsorted(cat_rows, sample)))
    print(f"  row/blob_id check against rows.parquet: {ok}")

    if n_fetch:
        hits = 0
        for r in c[:n_fetch]:
            bid = rows[int(r)].as_py()
            path = cat["path"][int(np.searchsorted(cat_rows, int(r)))].as_py()
            src = fetch(bid)
            hits += bool(find_in_tree(make_parser(lang, path).parse(src).root_node, pat))
        print(f"  fetched {min(n_fetch, c.size)} candidates from SWH: {hits} contain a match")


if __name__ == "__main__":
    main()
