"""Build the per-file structural catalog (catalog/) from a sampled corpus.

Reads a JSONL sample (blob_id, repo, path, language), parses each blob, computes
per-file AST features + shape hashes, and writes:

  catalog.parquet    one row per blob (metadata + features)
  functions.parquet  one row per function/method/class definition
  shapes.parquet     shape clusters with counts and a sample occurrence
  manifest.json      provenance, sampling, parser versions
  metrics.json       ingest throughput, sizes, parse errors
"""

from __future__ import annotations

import json
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq

from swhforest.blobstore import BlobStore
from swhforest.dag import make_parser
from swhforest.features import analyze

SAMPLE = sys.argv[1] if len(sys.argv) > 1 else "data/sample_100k.jsonl"
CACHE = sys.argv[2] if len(sys.argv) > 2 else "data/blobs"
OUT = sys.argv[3] if len(sys.argv) > 3 else "catalog"

rows = [json.loads(line) for line in open(SAMPLE)]
print(f"{len(rows):,} sampled blobs", flush=True)

# Licence / quality metadata comes from the stack-edu shards (no blob download).
HF_SHARDS = ["data/raw/py-train-00000.parquet", "data/raw/js-train-00000.parquet"]
want = {r["blob_id"] for r in rows}
hf: dict[str, dict] = {}
for shard in HF_SHARDS:
    pf = pq.ParquetFile(shard)
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(rg, columns=[
            "blob_id", "src_encoding", "score", "int_score",
            "detected_licenses", "license_type"])
        ids = t["blob_id"].to_pylist()
        keep = [i for i, b in enumerate(ids) if b in want and b not in hf]
        if not keep:
            continue
        sub = t.take(keep).to_pylist()
        for r in sub:
            hf.setdefault(r["blob_id"], r)
print(f"{len(hf):,} blobs matched in HF shards", flush=True)
store = BlobStore(CACHE)
parsers: dict[str, object] = {}

features: list[dict] = []
defs: list[dict] = []
t0 = time.time()
errors = 0
for i, r in enumerate(rows):
    lang = r["language"].lower()
    if lang in ("js", "jsx"):
        lang = "javascript"
    if lang not in parsers:
        parsers[lang] = make_parser(lang)
    try:
        src = store.reopen(r["blob_id"])
        feats, ds = analyze(src, lang, parsers[lang])
    except Exception as e:
        errors += 1
        print(f"  skip {r['blob_id']}: {e}", flush=True)
        continue
    h = hf.get(r["blob_id"], {})
    # `bytes` (features) = real SWH blob length; `source_bytes` = HF `length_bytes`
    # (they differ for blobs whose line endings were normalised in the shard).
    feats.update(blob_id=r["blob_id"], repo=r["repo"], path=r["path"],
                 source_bytes=r["length_bytes"],
                 license_type=h.get("license_type"),
                 detected_licenses=h.get("detected_licenses") or [],
                 edu_score=h.get("score"),
                 edu_int_score=h.get("int_score"),
                 src_encoding=h.get("src_encoding"))
    features.append(feats)
    for d in ds:
        d.update(blob_id=r["blob_id"], language=lang,
                 parse_error=feats["parse_error"])
        defs.append(d)
    if (i + 1) % 1000 == 0:
        dt = time.time() - t0
        print(f"  {i + 1}/{len(rows)} files, {(i + 1) / dt:.0f} files/s, "
              f"{len(defs):,} defs", flush=True)

dt = time.time() - t0
import os
os.makedirs(OUT, exist_ok=True)
pq.write_table(pa.Table.from_pylist(features), f"{OUT}/catalog.parquet")
pq.write_table(pa.Table.from_pylist(defs), f"{OUT}/functions.parquet")

ft = pa.Table.from_pylist(defs)
# sample occurrence = one real row (same blob, start and end), the first in
# (blob_id, start) order -- never a mix of min(blob) with min(start)/max(end).
ft_sorted = ft.sort_by([("blob_id", "ascending"), ("start", "ascending")])
ft_sorted = ft_sorted.append_column("_i", pa.array(range(ft_sorted.num_rows), pa.int64()))
shapes = ft_sorted.group_by(["language", "kind", "shape"]).aggregate([
    ("blob_id", "count"), ("nodes", "mean"), ("_i", "min"),
])
first = ft_sorted.take(shapes["_i_min"])
shapes = shapes.append_column("sample_blob", first["blob_id"]) \
               .append_column("sample_start", first["start"]) \
               .append_column("sample_end", first["end"])
shapes = shapes.select(["language", "kind", "shape", "blob_id_count", "nodes_mean",
                        "sample_blob", "sample_start", "sample_end"])
shapes = shapes.rename_columns([
    "language", "kind", "shape_id", "count", "mean_nodes",
    "sample_blob", "sample_start", "sample_end",
])
shapes = shapes.sort_by([("count", "descending")])
pq.write_table(shapes, f"{OUT}/shapes.parquet")

meta = json.load(open("data/sample_100k.meta.json"))
import tree_sitter
import tree_sitter_python
import tree_sitter_javascript
from importlib.metadata import version as _v
meta.update({
    "dataset_version": "0.1.1",
    "source_revision": {"repo": "HuggingFaceTB/stack-edu",
                        "revision": "eeec5caac5cc3758a18f1d3ba4416837a9ba814c"},
    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "files_ok": len(features),
    "files_error": errors,
    "parse_errors": sum(1 for f in features if f["parse_error"]),
    "definitions": len(defs),
    "shape_clusters": shapes.num_rows,
    "parser_versions": {
        "tree-sitter": _v("tree-sitter"),
        "tree-sitter-python": _v("tree-sitter-python"),
        "tree-sitter-javascript": _v("tree-sitter-javascript"),
        "pyarrow": _v("pyarrow"),
        "zstandard": _v("zstandard"),
        "numpy": _v("numpy"),
    },
    "bytes_note": "catalog.bytes = real SWH blob length; source_bytes = HF length_bytes",
})
json.dump(meta, open(f"{OUT}/manifest.json", "w"), indent=1)

metrics = {
    "files": len(features),
    "seconds": round(dt, 1),
    "files_per_second": round(len(features) / dt, 1),
    "source_bytes": sum(f["source_bytes"] for f in features),
    "parse_errors": sum(1 for f in features if f["parse_error"]),
    "definitions": len(defs),
    "shape_clusters": shapes.num_rows,
    "sizes": {
        "catalog.parquet": os.path.getsize(f"{OUT}/catalog.parquet"),
        "functions.parquet": os.path.getsize(f"{OUT}/functions.parquet"),
        "shapes.parquet": os.path.getsize(f"{OUT}/shapes.parquet"),
    },
}
json.dump(metrics, open(f"{OUT}/metrics.json", "w"), indent=1)
print(json.dumps(metrics, indent=1))
