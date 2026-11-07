"""Per-language statistics of the release (data/release/<lang>/).  Writes results/release_stats.json."""
import glob
import json
import os

import duckdb

con = duckdb.connect()
con.execute("SET threads=8")
out = {}
for d in sorted(glob.glob("data/release/*/ingest_metrics.json")):
    L = d.split("/")[2]
    R = f"data/release/{L}"
    m = json.load(open(d))
    sel = json.load(open(f"{R}/selection.json"))
    c = con.execute(f"""select count(*), count(distinct repo), sum(bytes), sum(parse_error::int),
                               sum(has_docstring::int), sum(n_defs) from read_parquet('{R}/catalog.parquet')""").fetchall()[0]
    f = con.execute(f"select count(*) from read_parquet('{R}/functions.parquet')").fetchall()[0][0]
    k = dict(con.execute(f"select kind, count(*) from read_parquet('{R}/functions.parquet') group by kind").fetchall())
    big = con.execute(f"""with g as (select shape, count(*) n from read_parquet('{R}/functions.parquet') group by shape)
        select sum(case when g.n > 100 then 1 else 0 end), sum(case when g.n > 100 and f.nodes >= 20 then 1 else 0 end)
        from read_parquet('{R}/functions.parquet') f join g using (shape)""").fetchall()[0]
    idx = sum(s["bytes"] for s in m["shards"])
    out[L] = {"rows_total": sel["rows_total"], "rows_in_size": sel["rows_200B_200kB"], "selected": sel["selected_blobs"],
              "blobs": c[0], "repos": c[1], "text_bytes": int(c[2]), "parse_errors": int(c[3]),
              "documented": int(c[4]), "definitions": f, "kinds": k,
              "boiler_defs": int(big[0] or 0), "boiler_defs_min20": int(big[1] or 0),
              "missing": m["missing_blobs"], "ingest_seconds": m["ingest_seconds"],
              "blobs_per_second": m["blobs_per_second"], "index_bytes": idx, "index_shards": len(m["shards"]),
              "index_features": sum(s["features"] for s in m["shards"]),
              "index_postings": sum(s["postings"] for s in m["shards"]),
              "table_bytes": sum(m["sizes"].values()), "soundness": m.get("soundness")}
    if L == "python":
        q = con.execute(f"""select count(*), coalesce(sum(bytes), 0) from read_parquet('{R}/catalog.parquet')
                            where bytes > 10000 and has_docstring and n_functions >= 5""").fetchall()[0]
        b = con.execute(f"""select count(*), coalesce(sum(length_bytes), 0) from read_parquet('{R}/rows.parquet')
                            where length_bytes > 10000""").fetchall()[0]
        import time
        t0 = time.perf_counter()
        for _ in range(5):
            con.execute(f"select count(*) from read_parquet('{R}/catalog.parquet') where bytes > 10000 and has_docstring and n_functions >= 5").fetchall()
        out[L]["curation"] = {"matches": q[0], "bytes": int(q[1]), "size_only_blobs": b[0], "size_only_bytes": int(b[1]),
                              "duckdb_ms_mean": round(1000 * (time.perf_counter() - t0) / 5, 1)}
    # the release selection itself is a view
    import hashlib
    ids = [r[0] for r in con.execute(f"select blob_id from read_parquet('{R}/catalog.parquet') order by blob_id").fetchall()]
    man = {"view": f"release_{L}", "dataset_version": "0.2.0",
           "source_revision": {"repo": "HuggingFaceTB/stack-edu", "revision": sel["source_revision"]},
           "predicate": f"length_bytes between 200 and 200000 and substr(blob_id,1,8) < '{sel['hash_threshold_hex']}' (40% of the hash space), retrievable from SWH",
           "n_blobs": len(ids), "blob_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}
    from importlib.metadata import version as _v
    from swhforest.grammars import GRAMMARS
    man["parser_versions"] = {"tree-sitter": _v("tree-sitter")}
    for g, (mod, _) in GRAMMARS.items():
        try:
            man["parser_versions"][mod.replace("_", "-")] = _v(mod.replace("_", "-"))
        except Exception:
            pass
    json.dump(man, open(f"{R}/view_release.json", "w"), indent=1)
    out[L]["view_sha256"] = man["blob_ids_sha256"]
    print(L, {k2: v for k2, v in out[L].items() if k2 != "soundness"}, flush=True)
os.makedirs("results", exist_ok=True)
json.dump(out, open("results/release_stats.json", "w"), indent=1)
