"""Example 3: structural search with the structural index (catalog/structure.parquet).

Runs one of the built-in queries of ``swhforest.queries`` (default: ``nested_fn``,
a Python function defined inside another function).  The index compiles the
pattern into the features that every match must contain (labelled edges E,
literals in context T, ancestor-descendant pairs A), intersects their postings
and returns the candidate catalog rows.  Pruning is sound: no matching blob is
ever outside the candidates.

* If ``is_exact(pattern)`` holds (a single relation: X contains Y, or X.field is
  the literal t), the candidates are the answer and no blob is fetched.
* Otherwise every candidate is fetched (from the local cache, or from the
  Software Heritage S3 bucket when it is not cached), parsed with tree-sitter
  and checked with the exact matcher ``treematch.find_in_tree``.

The script finishes with an exact query (``except_pass``) to show the
no-fetch path.

Usage:
    PYTHONPATH=src python examples/03_query_structural.py [query] [catalog_dir] [cache_dir]

The first run of nested_fn downloads the 2,276 candidate blobs (15.7 MB of text,
gzip-compressed on S3) into ``data/blobs``; later runs read the cache.

Expected output (100k build; timings vary):
  === Example 3: Structural Search with the Structural Index ===
  Loaded catalog/structure.parquet: 1,537,956 features, 31,593,726 postings (... s)

  Query nested_fn (python): function defined inside a function
    pattern features:   4
    candidates:         2,276 of 50,000 python blobs (4.55%)
    exact (no fetch):   False
    fetched from SWH:   2,276 blobs (... s)        [first run only]
    fetched/parsed:     2,276 blobs (15.7 MB of text)
    verified matches:   2,276 blobs, 3,610 occurrences
    candidates without a match: 0
      9a669a35b426fd2c05a6bd4106e3a3f2acf09ffd [494:1337]  yuvipanda/kubernetes-informers:/tests/test_coalesce.py
      68a28ef6d1a10b19e4e726c04938d70a214c50e6 [255:2584]  GUZHIXIANG/spatial_concept:/simulator.py
      c1f4e08beb441f484e02362c9947818ce83746dd [46:365]  faterazer/LeetCode:/1376. Time Needed to Inform All Employees/Solution.py

  Query except_pass (python): except clause containing pass
    candidates:         1,106 of 50,000 python blobs (2.21%)
    exact (no fetch):   True
    answer:             1,106 blobs, 0 fetched
"""

import sys
import time

import pyarrow.parquet as pq

from swhforest.blobstore import BlobStore
from swhforest.dag import make_parser
from swhforest.queries import QUERIES
from swhforest.structindex import StructIndex, is_exact, pattern_features
from swhforest.treematch import find_in_tree


def main():
    qname = sys.argv[1] if len(sys.argv) > 1 else "nested_fn"
    cdir = sys.argv[2] if len(sys.argv) > 2 else "catalog"
    cache = sys.argv[3] if len(sys.argv) > 3 else "data/blobs"
    print("=== Example 3: Structural Search with the Structural Index ===")

    t0 = time.perf_counter()
    idx = StructIndex.load(f"{cdir}/structure.parquet")
    print(f"Loaded {cdir}/structure.parquet: {idx.features.size:,} features, "
          f"{idx.blobs.size:,} postings ({time.perf_counter() - t0:.2f} s)")
    cat = pq.read_table(f"{cdir}/catalog.parquet", columns=["blob_id", "repo", "path"])
    ids = cat["blob_id"].to_pylist()

    lang, pat, desc = QUERIES[qname]
    n_lang = idx.lang_rows(lang).size
    cand = idx.candidates(pat, lang)
    if cand is None:  # the pattern implies no feature: scan the whole language
        cand = idx.lang_rows(lang)
    exact = is_exact(pat, lang)
    print(f"\nQuery {qname} ({lang}): {desc}")
    print(f"  pattern features:   {len(pattern_features(pat, lang))}")
    print(f"  candidates:         {cand.size:,} of {n_lang:,} {lang} blobs ({100 * cand.size / n_lang:.2f}%)")
    print(f"  exact (no fetch):   {exact}")

    if exact:
        print(f"  answer:             {cand.size:,} blobs, 0 fetched")
    else:
        store = BlobStore(cache)
        cand_ids = [ids[int(r)] for r in cand]
        t0 = time.perf_counter()
        avail = set(store.ensure_local(cand_ids))  # fetches from SWH S3 what is not cached
        if store.fetches:
            print(f"  fetched from SWH:   {store.fetches:,} blobs ({time.perf_counter() - t0:.1f} s)")
        parser = make_parser(lang)
        hits, occ, samples = 0, 0, []
        for r, bid in zip(cand, cand_ids):
            if bid not in avail:
                continue
            spans = find_in_tree(parser.parse(store.reopen(bid)).root_node, pat)
            if spans:
                hits += 1
                occ += len(spans)
                if len(samples) < 3:
                    samples.append((int(r), bid, spans[0]))
        print(f"  fetched/parsed:     {store.reopens:,} blobs ({store.reopened_bytes / 1e6:.1f} MB of text)")
        if store.misses:
            print(f"  unavailable on SWH: {len(store.misses):,} blobs")
        print(f"  verified matches:   {hits:,} blobs, {occ:,} occurrences")
        print(f"  candidates without a match: {store.reopens - hits:,}")
        for r, bid, (s, e) in samples:
            print(f"    {bid} [{s}:{e}]  {cat['repo'][r]}:{cat['path'][r]}")

    # an exact (single-relation) query: the postings are the answer, nothing is fetched
    lang, pat, desc = QUERIES["except_pass"]
    cand = idx.candidates(pat, lang)
    n_lang = idx.lang_rows(lang).size
    print(f"\nQuery except_pass ({lang}): {desc}")
    print(f"  candidates:         {cand.size:,} of {n_lang:,} {lang} blobs ({100 * cand.size / n_lang:.2f}%)")
    print(f"  exact (no fetch):   {is_exact(pat, lang)}")
    print(f"  answer:             {cand.size:,} blobs, 0 fetched")


if __name__ == "__main__":
    main()
