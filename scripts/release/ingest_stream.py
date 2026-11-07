"""Streaming build of one language of the release: fetch from SWH, parse, write tables and index.

Blobs are fetched into memory and dropped after parsing; nothing is cached on disk.
Outputs in data/release/<lang>/:
  catalog.parquet, functions.parquet, shapes.parquet       feature tables
  structure-<k>.parquet                                    structural index, one shard per SHARD_ROWS rows
  missing.txt                                              blobs not retrievable from SWH
  ingest_metrics.json                                      counts, timings, soundness check
With TRUTH=1 the same pass runs every query defined for the language (built-in and Semgrep
workloads) and checks that the index candidates contain all matching blobs.
Usage: python scripts/release/ingest_stream.py <lang> [processes] [threads_per_process]
"""
import gzip
import json
import multiprocessing as mp
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from swhforest.features import analyze
from swhforest.grammars import make_parser
from swhforest.structindex import FAMILIES, StructIndex, blob_features, feature_hash
from swhforest.treematch import find_in_tree

S3 = "https://softwareheritage.s3.amazonaws.com/content/{}"
CHUNK = 2000
SHARD_ROWS = 2_000_000
FAM = {f: i for i, f in enumerate(FAMILIES)}
LANG = sys.argv[1]
PROCS = int(sys.argv[2]) if len(sys.argv) > 2 else 64
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 12
OUT = f"data/release/{LANG}"
TRUTH = os.environ.get("TRUTH") == "1"


def queries():
    qs = {}
    if not TRUTH:
        return qs
    from swhforest.queries import QUERIES
    qs.update({k: v[1] for k, v in QUERIES.items() if v[0] == LANG})
    try:
        from swhforest.semgrepqueries import SEMGREP_QUERIES
        qs.update({k: v[1] for k, v in SEMGREP_QUERIES.items() if v[0] == LANG})
    except ImportError:
        pass
    return qs


def fetch(blob_id):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(S3.format(blob_id), timeout=60) as r:
                return gzip.decompress(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1 + 2 * attempt)
        except Exception:
            time.sleep(1 + 2 * attempt)
    return None


def work(task):
    start, rows = task
    parsers = {}
    qs = queries()
    with ThreadPoolExecutor(THREADS) as ex:
        blobs = list(ex.map(fetch, [r["blob_id"] for r in rows]))
    feats_out, defs_out, missing = [], [], []
    H, F, R = [], [], []
    hits = {q: [] for q in qs}
    t0 = time.time()
    for k, (r, src) in enumerate(zip(rows, blobs)):
        if src is None:
            missing.append(r["blob_id"])
            continue
        row = start + k
        p = parsers.get(r["path"].lower().endswith(".tsx"))
        if p is None:
            p = parsers[r["path"].lower().endswith(".tsx")] = make_parser(LANG, r["path"])
        try:
            feats, ds = analyze(src, LANG, p)
            root = p.parse(src).root_node
        except Exception:
            missing.append(r["blob_id"])
            continue
        feats.update(row=row, blob_id=r["blob_id"], repo=r["repo"], path=r["path"],
                     source_bytes=r["length_bytes"], license_type=r["license_type"],
                     detected_licenses=list(r["detected_licenses"] or []), edu_score=r["edu_score"],
                     edu_int_score=r["edu_int_score"], src_encoding=r["src_encoding"])
        feats_out.append(feats)
        for d in ds:
            d.update(blob_id=r["blob_id"], language=LANG, parse_error=feats["parse_error"])
            defs_out.append(d)
        fs = blob_features(root)
        H.append(np.fromiter((feature_hash(f) for f in fs), dtype=np.uint64, count=len(fs)))
        F.append(np.fromiter((FAM[f[0]] for f in fs), dtype=np.uint8, count=len(fs)))
        R.append(np.full(len(fs), row, dtype=np.uint32))
        for q, pat in qs.items():
            if find_in_tree(root, pat):
                hits[q].append(row)
    cat = lambda a, t: np.concatenate(a) if a else np.empty(0, dtype=t)
    return start, feats_out, defs_out, missing, cat(H, np.uint64), cat(F, np.uint8), cat(R, np.uint32), hits, time.time() - t0


def main():
    table = pq.read_table(f"{OUT}/rows.parquet")
    n_rows = table.num_rows
    n_shards = (n_rows + SHARD_ROWS - 1) // SHARD_ROWS
    SUPER = 100 * CHUNK

    def results(pool):
        for s0 in range(0, n_rows, SUPER):
            part = table.slice(s0, SUPER).to_pylist()
            tasks = [(s0 + i, part[i:i + CHUNK]) for i in range(0, len(part), CHUNK)]
            yield from pool.imap_unordered(work, tasks)
    os.makedirs(f"{OUT}/_pairs", exist_ok=True)
    spill = {k: {x: open(f"{OUT}/_pairs/{k}.{x}", "wb") for x in "hfr"} for k in range(n_shards)}
    cat_w = fn_w = None
    cat_schema = fn_schema = None
    missing, hits = [], {}
    t0 = time.time()
    done = parse_s = 0
    with mp.Pool(PROCS) as pool:
        for n, (start, fl, dl, miss, h, f, r, hq, ps) in enumerate(results(pool)):
            if fl:
                t = pa.Table.from_pylist(fl, schema=cat_schema)
                if cat_w is None:
                    cat_schema = t.schema
                    cat_w = pq.ParquetWriter(f"{OUT}/catalog.parquet", cat_schema, compression="zstd")
                cat_w.write_table(t)
            if dl:
                t = pa.Table.from_pylist(dl, schema=fn_schema)
                if fn_w is None:
                    fn_schema = t.schema
                    fn_w = pq.ParquetWriter(f"{OUT}/functions.parquet", fn_schema, compression="zstd")
                fn_w.write_table(t)
            k = start // SHARD_ROWS
            spill[k]["h"].write(h.tobytes()); spill[k]["f"].write(f.tobytes()); spill[k]["r"].write(r.tobytes())
            missing += miss
            for q, v in hq.items():
                hits.setdefault(q, []).extend(v)
            done += len(fl) + len(miss)
            parse_s += ps
            if n % 25 == 0:
                el = time.time() - t0
                print(f"  {done:,}/{n_rows:,} blobs  {done / el:.0f} blobs/s  missing {len(missing):,}  "
                      f"{el / 60:.1f} min", flush=True)
    cat_w.close(); fn_w.close()
    for k in spill:
        for x in spill[k].values():
            x.close()
    t_ingest = time.time() - t0
    open(f"{OUT}/missing.txt", "w").write("\n".join(missing))

    # tables in row order (row = position of the blob in rows.parquet, sorted by blob_id)
    import duckdb
    con = duckdb.connect()
    con.execute(f"copy (select * from read_parquet('{OUT}/catalog.parquet') order by row) "
                f"to '{OUT}/catalog.sorted.parquet' (format parquet, compression zstd)")
    os.replace(f"{OUT}/catalog.sorted.parquet", f"{OUT}/catalog.parquet")
    # shapes
    con.execute(f"""
        copy (select language, kind, shape as shape_id, count(*) as count, avg(nodes) as mean_nodes,
                     arg_min(blob_id, start) as sample_blob, min(start) as sample_start,
                     arg_min("end", start) as sample_end
              from read_parquet('{OUT}/functions.parquet') group by language, kind, shape
              order by count desc) to '{OUT}/shapes.parquet' (format parquet, compression zstd)""")

    # structural index, one shard per SHARD_ROWS rows (row numbers are global within the language)
    t1 = time.time()
    blob_lang = np.zeros(n_rows, dtype=np.uint8)
    shard_meta = []
    for k in range(n_shards):
        h = np.fromfile(f"{OUT}/_pairs/{k}.h", dtype=np.uint64)
        f = np.fromfile(f"{OUT}/_pairs/{k}.f", dtype=np.uint8)
        r = np.fromfile(f"{OUT}/_pairs/{k}.r", dtype=np.uint32)
        idx = StructIndex.from_pairs(h, f, r, [LANG], blob_lang)
        path = f"{OUT}/structure-{k:03d}.parquet"
        idx.save(path)
        shard_meta.append({"shard": k, "features": int(idx.features.size), "postings": int(idx.blobs.size),
                           "bytes": os.path.getsize(path)})
        del h, f, r, idx
        for x in "hfr":
            os.remove(f"{OUT}/_pairs/{k}.{x}")
    t_index = time.time() - t1

    m = {"language": LANG, "selected_blobs": n_rows, "ingested_blobs": n_rows - len(missing),
         "missing_blobs": len(missing), "ingest_seconds": round(t_ingest, 1),
         "blobs_per_second": round(n_rows / t_ingest, 1), "parse_cpu_seconds": round(parse_s, 1),
         "index_seconds": round(t_index, 1), "processes": PROCS, "threads": THREADS, "shards": shard_meta,
         "sizes": {f: os.path.getsize(f"{OUT}/{f}.parquet") for f in ("catalog", "functions", "shapes")}}
    if TRUTH and hits:
        from swhforest.structindex import ShardedIndex
        from swhforest.queries import QUERIES
        try:
            from swhforest.semgrepqueries import SEMGREP_QUERIES
        except ImportError:
            SEMGREP_QUERIES = {}
        pats = {k: v[1] for k, v in QUERIES.items() if v[0] == LANG}
        pats.update({k: v[1] for k, v in SEMGREP_QUERIES.items() if v[0] == LANG})
        sidx = ShardedIndex.load(OUT)
        m["soundness"] = {}
        ingested = n_rows - len(missing)
        for q, pat in pats.items():
            true = set(hits.get(q, []))
            c = sidx.candidates(pat, LANG)
            n_c = ingested if c is None else int(len(c))
            missed = 0 if c is None else len(true - set(c.tolist()))
            m["soundness"][q] = {"matching_blobs": len(true), "candidates": n_c, "missed": missed,
                                 "exact": sidx.last_exact}
            print(q, m["soundness"][q], flush=True)
        assert all(v["missed"] == 0 for v in m["soundness"].values()), "unsound pruning"
    json.dump(m, open(f"{OUT}/ingest_metrics.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in m.items() if k != "soundness"}, indent=1))


if __name__ == "__main__":
    main()
