# SWHForest — 100k Python/JavaScript Sample (`catalog/`)

This directory holds the self-contained **100k benchmark sample** of SWHForest: 100,000 Stack-Edu files (50,000 Python and 50,000 JavaScript, reservoir-sampled with seed `20261025` from the first Stack-Edu shard of each language). It is used by `examples/01_curate_views.py`, `examples/02_inspect_boilerplate.py`, and `examples/03_query_structural.py`.

For the full 10-language release (`52.1M` files), see `data/release/<language>/` and the top-level [`README.md`](../README.md). Column-level documentation: [`SCHEMA.md`](SCHEMA.md).

## Files

| File | Granularity | Size |
| :--- | :--- | :--- |
| `catalog.parquet` | 1 row per file (`blob_id`) | 100,000 rows (14.2 MB) |
| `functions.parquet` | 1 row per definition (`function`, `method`, `class`) | 613,950 rows (17.7 MB) |
| `shapes.parquet` | 1 row per `(language, kind, shape)` cluster | 466,609 rows (15.8 MB) |
| `structure.parquet` | 1 row per structural-index feature | 1,537,956 rows (40.6 MB) |
| `views/*.json` | Reproducible view manifests (`all`, `docstring`, `no_boilerplate`) | 3 views |
| `manifest.json`, `metrics.json`, `structure_metrics.json`, `views.json` | Provenance & build metrics | JSON |

## Quick Examples on `catalog/`

```python
import swhforest
from swhforest import Pattern

# 1. Structural search on the 100k sample
p = Pattern(type="except_clause", contains=Pattern(type="pass_statement"))
res = swhforest.search("catalog", p, language="python")
print(res["exact"], res["n_candidates"])  # True, 1106

# 2. Curate a view from catalog/catalog.parquet
view = swhforest.curate_view(
    "catalog",
    where="language = 'python' AND bytes > 10000 AND has_docstring AND n_functions >= 5",
)
print(view["n_blobs"], view["total_bytes"])  # 599, 12927500
```
