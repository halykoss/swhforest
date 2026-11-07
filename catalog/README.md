# 100k sample

This directory holds the **100k sample** of SWHForest: 100,000 Stack-Edu
blobs (50,000 Python and 50,000 JavaScript, reservoir-sampled with seed 20261025 from
the first Stack-Edu shard of each language). It is the input of examples 01–03; it is *not* the ten-language release, which
lives in `data/release/<language>/` (see the top-level README).

Git tracks only the documentation and the small JSON files of this directory; the
Parquet files and `views/` are distributed with the data release. No source text is
distributed: every row points to a Software Heritage content object by its `blob_id`
(`sha1_git`). Rebuild it with the scripts in `scripts/sample/`.

Column-level documentation is in [`SCHEMA.md`](SCHEMA.md).

## Released files

| File | One row per | Size |
|---|---|---|
| `catalog.parquet` | blob | 100,000 rows, 14.2 MB |
| `functions.parquet` | definition (function, method, class) | 613,950 rows, 17.7 MB |
| `shapes.parquet` | shape cluster (language, kind, shape) | 466,609 rows, 15.8 MB |
| `structure.parquet` | structural-index feature | 1,537,956 rows, 40.6 MB |
| `views/*.json` | view manifest | 3 views |
| `manifest.json` | provenance: sample, seed, source revision, parser versions | yes |
| `metrics.json` | build metrics (rows, sizes, parse time) | yes |
| `structure_metrics.json` | index sizes and the soundness check of the built-in queries | yes |
| `views.json` | view cardinalities, text bytes and checksums | yes |

Sizes are bytes / 10^6, from `metrics.json` and `structure_metrics.json`.
`catalog`, `functions` and `shapes` are Snappy-compressed (the pyarrow default);
`structure.parquet` is zstd level 12.

### Views (100k build)

| View | Predicate | Blobs | Text | Manifest | `blob_ids_sha256` |
|---|---|---:|---:|---:|---|
| `all` | `true` | 100,000 | 276.8 MB | 4.4 MB | `7c1b3310ad3ee68341206f6af2c394992bcfcff098cca8d042873a72d50a310c` |
| `docstring` | `catalog.has_docstring = true` | 18,088 | 87.7 MB | 0.8 MB | `7b45e556ecd160f552b72532b9c78757bb907c494df80b41258a1f6c3b9c43b0` |
| `no_boilerplate` | blob has no definition whose shape hash (any language or kind) occurs in >100 rows of `functions.parquet` | 80,453 | 194.9 MB | 3.5 MB | `8175033cf90beacc867a568de1b572a853bef6d88d67a9f5452d914005549b5c` |

## Quick examples

### Metadata filter with DuckDB

```python
import duckdb
con = duckdb.connect()
n, size = con.execute("""
    SELECT count(*), sum(bytes)
    FROM 'catalog/catalog.parquet'
    WHERE language = 'python' AND bytes > 10000
      AND has_docstring AND n_functions >= 5
""").fetchone()
print(n, size)   # 599 12927500  (12.9 MB of the 276.8 MB build)
```

### Structural query with the index

```python
import pyarrow.parquet as pq
from swhforest.queries import QUERIES
from swhforest.structindex import StructIndex, is_exact

idx = StructIndex.load("catalog/structure.parquet")
ids = pq.read_table("catalog/catalog.parquet", columns=["blob_id"])["blob_id"].to_pylist()
lang, pat, _ = QUERIES["nested_fn"]
rows = idx.candidates(pat, lang)              # catalog row numbers (numpy uint32)
print(len(rows), is_exact(pat, lang))         # 2276 False -> fetch and check the candidates
candidate_ids = [ids[r] for r in rows]
```

`examples/03_query_structural.py` fetches the candidates from Software Heritage,
parses them and runs the exact matcher.

### Reading and checking a view

```python
import hashlib, json
view = json.load(open("catalog/views/no_boilerplate.json"))
digest = hashlib.sha256("\n".join(view["blobs"]).encode()).hexdigest()
assert digest == view["blob_ids_sha256"]
print(view["view"], view["n_blobs"], view["text_bytes"])   # no_boilerplate 80453 194940865
```
