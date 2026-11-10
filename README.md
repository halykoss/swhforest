# SWHForest

<p align="center">
  <a href="https://arxiv.org/abs/2611.11439"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2611.11439-b31b1b.svg?logo=arxiv"></a>
  <a href="https://huggingface.co/datasets/halykoss/swhforest"><img alt="Dataset" src="https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-halykoss%2Fswhforest-ffd21e.svg"></a>
  <a href="https://github.com/halykoss/swhforest"><img alt="Code" src="https://img.shields.io/badge/GitHub-Code-181717.svg?logo=github"></a>
</p>

**SWHForest** lets you filter, search by AST structure, and detect clones across **52.1M files** (**340.6M functions/classes** in 10 languages of [Stack-Edu](https://huggingface.co/datasets/HuggingFaceTB/stack-edu)) **before downloading source code** from the Software Heritage Archive (SWH).

Instead of redistributing source text, SWHForest ships compact Parquet tables and a sharded AST index keyed by `blob_id` (Git SHA-1). You select or match files locally and download only the surviving candidates from SWH.

---

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # pinned versions used for the release
pip install -e ".[all-languages,dev]"    # package + all 10 Tree-sitter grammars + pytest
```

Or run the self-contained Docker image (when built with `catalog/` and `data/release/rust/` present):

```bash
docker build -t swhforest . && docker run --rm swhforest
```

---

## Quick Examples

### 1. Search by AST Pattern (`swhforest.search` + `ast-grep`)

Find files matching a syntactic pattern. Single-relation patterns (`exact=True`) are answered directly by the index with **0 downloads**; multi-part patterns prune the corpus locally and verify surviving candidates with [`ast-grep`](https://ast-grep.github.io/).

```python
import swhforest
from swhforest import Pattern

# A) Single-block query -> exact answer from the local index (0 files downloaded)
#    Match Python `except` blocks containing `pass`:
p_exact = Pattern(type="except_clause", contains=Pattern(type="pass_statement"))
res = swhforest.search("data/release/python", p_exact, language="python")
print(res["exact"], res["n_candidates"])  # True, 207111

# B) Multi-block query -> local index pruning + live SWH fetch + ast-grep verification
#    Match Rust `for` loops calling `.clone()`:
call_clone = Pattern(type="call_expression", fields={
    "function": Pattern(type="field_expression", fields={
        "field": Pattern(type="field_identifier", text="clone")
    })
})
p_loop = Pattern(type="for_expression", contains=call_clone)
res = swhforest.search("data/release/rust", p_loop, language="rust", verify_limit=10)
print(res["n_candidates"], len(res["verified_blob_ids"]))
```

---

### 2. Find Renamed Clones & Screen Benchmarks (`swhforest.find_clones`)

Every function, method, and class has a 64-bit rename-invariant AST Merkle hash (`shape`). Pass any snippet (e.g., a HumanEval/MBPP solution) to find structurally identical definitions across the corpus and slice their bytes directly from SWH:

```python
import swhforest

code = """
def has_close_elements(numbers, threshold):
    for idx, elem in enumerate(numbers):
        for idx2, elem2 in enumerate(numbers):
            if idx != idx2:
                if abs(elem - elem2) < threshold:
                    return True
    return False
"""

matches = swhforest.find_clones("data/release/python", code, language="python", min_nodes=20, limit=5)
for m in matches:
    print(f"{m['repo']}:{m['path']} -> blob {m['blob_id']} [{m['start_byte']}:{m['end_byte']}]")
    print(swhforest.slice_definition(m["blob_id"], m["start_byte"], m["end_byte"]))
```

---

### 3. Curate Subsets & Export a Reproducible View (`swhforest.curate_view`)

Filter files by their 17 syntactic metrics in `catalog.parquet` (file size, docstrings, function/class counts, AST depth, parse errors) and export a lightweight JSON manifest (`blob_id` list + SHA-256 checksum) so others can rebuild the exact same subset:

```python
import swhforest

view = swhforest.curate_view(
    "data/release/python",
    where="bytes > 10000 AND has_docstring = true AND (n_functions + n_methods) >= 5",
    out_manifest="curated_python.json",
)
print(f"{view['n_blobs']:,} files ({view['total_bytes'] / 1e9:.2f} GB) | SHA-256: {view['blob_ids_sha256'][:16]}...")

# Verify any release directory against its published manifest
assert swhforest.verify_view("data/release/rust", "data/release/rust/view_release.json")
```

> **Tip:** All tables are standard Parquet (`rows.parquet`, `catalog.parquet`, `functions.parquet`, `shapes.parquet`, `structure-<k>.parquet`), so you can also query them directly from DuckDB, Polars, or Spark. Full column reference: [`SCHEMA.md`](SCHEMA.md).

---

## Runnable Scripts (`examples/`)

Download the data from [Hugging Face](https://huggingface.co/datasets/halykoss/swhforest) into the repository root (the layout matches), then run from the repository root:

```bash
pip install -U huggingface_hub                                                             # provides the `hf` command
hf download halykoss/swhforest --repo-type dataset --include "catalog/*" --local-dir .            # 100k sample (~100 MB)
hf download halykoss/swhforest --repo-type dataset --include "data/release/rust/*" --local-dir .  # one language
```


```bash
PYTHONPATH=src python -m pytest -q tests                                       # Unit & property tests (no data needed)
PYTHONPATH=src python examples/01_curate_views.py                              # Use Case 1 on 100k sample (offline)
PYTHONPATH=src python examples/02_inspect_boilerplate.py                       # Use Case 2 on 100k sample (offline)
PYTHONPATH=src python examples/03_query_structural.py                          # Use Case 3 on 100k sample
PYTHONPATH=src python examples/04_query_release.py data/release/rust --fetch 5 # Full Rust release + SWH fetch
```

---

## Public Dataset (v0.2.0 — 40% Sample of Stack-Edu)

Available at **[huggingface.co/datasets/halykoss/swhforest](https://huggingface.co/datasets/halykoss/swhforest)** (CC-BY-4.0; Parquet files can be queried in place with DuckDB via `hf://datasets/halykoss/swhforest/...`).

Deterministic 40% SHA-1 prefix sample (`blob_id < 66666666`) of deduplicated 200 B–200 kB files across the 10 largest languages in Stack-Edu (revision `eeec5ca`):

| Language | Files (`blob_id`) | Definitions | Source Text (Not Stored) | Tables (GB) | Index (GB) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| **Java** | 17,429,408 | 135,578,481 | 53.94 GB | 6.15 | 7.52 |
| **Python** | 9,481,411 | 43,406,601 | 27.10 GB | 2.82 | 3.16 |
| **C++** | 6,453,978 | 39,997,408 | 17.31 GB | 2.25 | 2.71 |
| **JavaScript** | 5,173,711 | 39,520,638 | 13.77 GB | 2.04 | 1.77 |
| **C#** | 4,560,555 | 30,471,416 | 13.74 GB | 1.65 | 1.81 |
| **PHP** | 3,904,782 | 20,813,360 | 11.24 GB | 1.17 | 0.86 |
| **C** | 2,266,959 | 8,370,541 | 6.21 GB | 0.64 | 0.74 |
| **TypeScript** | 1,673,249 | 12,517,615 | 3.85 GB | 0.66 | 0.66 |
| **Go** | 752,530 | 4,823,599 | 1.84 GB | 0.26 | 0.26 |
| **Rust** | 448,639 | 5,149,143 | 2.12 GB | 0.23 | 0.29 |
| **Total** | **52,145,222** | **340,648,802** | **151.12 GB** | **17.87** | **19.77** |

To rebuild a release from upstream Stack-Edu metadata and SWH blobs, see `scripts/release/` (`download_stackedu_meta.py`, `select_release.py`, `ingest_stream.py`, `release_stats.py`).

---

## Authors, License & Citation

- **Authors:** Federico Pennino, Andrea Gurioli, Maurizio Gabbrielli (University of Bologna), and Paolo Ferragina (Sant'Anna School of Advanced Studies, Pisa).
- **Code:** MIT ([`LICENSE`](LICENSE)) · **Dataset tables & index:** CC-BY-4.0 · **Source blobs:** fetched on demand from Software Heritage under their original upstream licenses.

```bibtex
@misc{pennino2026swhforest,
  author        = {Pennino, Federico and Gurioli, Andrea and Gabbrielli, Maurizio and Ferragina, Paolo},
  title         = {{SWHForest}: A Structural Curation and Search Layer for Large-Scale Code Corpora},
  year          = {2026},
  eprint        = {2611.11439},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2611.11439}
}
```
