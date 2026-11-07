"""Build catalog/structure.parquet (the structural index) for one build.

Usage: python scripts/sample/build_structindex.py <catalog_dir> [workers] [--truth]
With --truth, the same pass also runs every query of swhforest.queries on every blob
and checks that the index's candidate sets contain all matching blobs (soundness check).
"""
import json
import multiprocessing as mp
import os
import sys
import time

import numpy as np
import pyarrow.parquet as pq

from swhforest.blobstore import BlobStore
from swhforest.dag import make_parser
from swhforest.queries import QUERIES
from swhforest.structindex import FAMILIES, StructIndex, blob_features, feature_hash
from swhforest.treematch import find_in_tree

FAM = {f: i for i, f in enumerate(FAMILIES)}


def work(args):
    lang, chunk, truth = args
    store, parser = BlobStore("data/blobs"), make_parser(lang)
    qs = {q: p for q, (l, p, _) in QUERIES.items() if l == lang} if truth else {}
    hs, fs, rs, hits = [], [], [], {q: [] for q in qs}
    for row, bid in chunk:
        tree = parser.parse(store.reopen(bid))
        feats = blob_features(tree.root_node)
        hs.append(np.fromiter((feature_hash(f) for f in feats), dtype=np.uint64, count=len(feats)))
        fs.append(np.fromiter((FAM[f[0]] for f in feats), dtype=np.uint8, count=len(feats)))
        rs.append(np.full(len(feats), row, dtype=np.uint32))
        for q, p in qs.items():
            if find_in_tree(tree.root_node, p):
                hits[q].append(row)
    cat = lambda a, t: np.concatenate(a) if a else np.empty(0, dtype=t)
    return cat(hs, np.uint64), cat(fs, np.uint8), cat(rs, np.uint32), hits


def main():
    d = sys.argv[1]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
    truth = "--truth" in sys.argv
    cat = pq.read_table(f"{d}/catalog.parquet", columns=["blob_id", "language"])
    ids, langs = cat["blob_id"].to_pylist(), cat["language"].to_pylist()
    languages = sorted(set(langs))
    blob_lang = np.array([languages.index(l) for l in langs], dtype=np.uint8)
    tasks = []
    for lang in languages:
        rows = [(i, b) for i, (b, l) in enumerate(zip(ids, langs)) if l == lang]
        tasks += [(lang, rows[k:k + 1000], truth) for k in range(0, len(rows), 1000)]
    t0 = time.time()
    H, F, R, hits = [], [], [], {}
    with mp.Pool(workers) as pool:
        for n, (h, f, r, hq) in enumerate(pool.imap_unordered(work, tasks)):
            H.append(h); F.append(f); R.append(r)
            for q, v in hq.items():
                hits.setdefault(q, []).extend(v)
            if n % 50 == 0:
                print(f"  {n}/{len(tasks)} chunks, {time.time() - t0:.0f}s", flush=True)
    t_extract = time.time() - t0
    idx = StructIndex.from_pairs(np.concatenate(H), np.concatenate(F), np.concatenate(R), languages, blob_lang)
    path = f"{d}/structure.parquet"
    idx.save(path)
    t_total = time.time() - t0
    m = {"blobs": len(ids), "features": int(idx.features.size), "postings": int(idx.blobs.size),
         "per_family": {f: {"features": int((idx.families == i).sum()),
                            "postings": int(idx.df[idx.families == i].sum())} for i, f in enumerate(FAMILIES)},
         "bytes": os.path.getsize(path), "extract_seconds": round(t_extract, 1),
         "build_seconds": round(t_total, 1), "workers": workers}
    if truth:
        idx = StructIndex.load(path)
        m["soundness"] = {}
        for q, (lang, p, _) in QUERIES.items():
            true = set(hits.get(q, []))
            c = idx.candidates(p, lang)
            cs = set(idx.lang_rows(lang).tolist()) if c is None else set(c.tolist())
            missed = len(true - cs)
            m["soundness"][q] = {"matching_blobs": len(true), "candidates": len(cs), "missed": missed}
            print(q, m["soundness"][q], flush=True)
            assert missed == 0, f"unsound pruning for {q}"
    json.dump(m, open(f"{d}/structure_metrics.json", "w"), indent=1)
    print(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
