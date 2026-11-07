# SWHForest: A Structural Curation and Search Layer for Pointer-Based Code Corpora

Modern large-scale code corpora such as **The Stack v2** and **Stack-Edu** distribute tables of Software Heritage (SWH) content identifiers (`blob_id`, the 40-hex Git SHA-1 of each file) rather than raw source text. While pointer-only distribution avoids redistributing terabytes of code, it creates a severe bottleneck for data curation and static analysis: selecting files by their syntactic content normally requires downloading and parsing millions of remote blobs first.

**SWHForest** provides a precomputed, source-free structural engine and public dataset for pointer-based code corpora. In a single streaming pass over remote blobs, its Tree-sitter pipeline extracts three structural representations per file and immediately discards the source text:

1. **Inverted AST Feature Index (`swhforest.structindex`, `swhforest.astgrep`):** decomposes each syntax tree into parent–child links (`E`), literals in syntactic roles (`T`), and ancestor–descendant nesting pairs (`A`). At query time, `swhforest` compiles structural patterns one-to-one into [`ast-grep`](https://ast-grep.github.io/) relational rules, resolves single-constraint patterns directly from the local index with **zero file downloads**, and prunes multi-clause patterns with **zero false negatives** before verifying surviving candidates.
2. **Rename-Invariant AST Merkle Fingerprints (`swhforest.features`):** strips identifier names and literal values from every `function`, `method`, and `class` (`340.6M` definitions across 10 languages), hashing the syntax tree bottom-up into a 64-bit Merkle `shape` accompanied by its `[start_byte, end_byte]` span for instant Type-2 clone lookup, boilerplate filtering, and benchmark contamination screening.
3. **Per-File Syntactic Profiles & Reproducible Views (`catalog.parquet`, `view_release.json`):** 17 AST, documentation, and complexity metrics per file (`52.1M` unique files), paired with SHA-256 view manifests (`view_release.json`) that pin parser versions and `blob_id` checksums so filtered subsets can be cited and rebuilt without redistributing code.

---

## Quick Start

### 1. Installation (Python $\ge$ 3.10)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # pinned versions used for the release
pip install -e ".[all-languages,dev]"    # package + the other eight grammars + pytest
```

### 2. Run the Prepackaged Examples (or via Docker)

The examples read the Parquet files of the 100k Python/JS sample (`catalog/`) and of one release language (`data/release/rust/`); both are distributed with the data release, not in git (place them there before running, or build the Docker image below from a checkout that has them):

```bash
# Structural index search + live SWH fetch & ast-grep verification
PYTHONPATH=src python examples/04_query_release.py data/release/rust --fetch 5

# Boilerplate & template cluster detection via 64-bit AST Merkle shapes
PYTHONPATH=src python examples/02_inspect_boilerplate.py

# Syntactic subset curation & transfer savings
PYTHONPATH=src python examples/01_curate_views.py

# Run the test suite (random AST pattern soundness, exactness, index round-trip; no data needed)
PYTHONPATH=src python -m pytest -q tests
```

Or run directly with Docker:

```bash
docker build -t swhforest .
docker run --rm swhforest
```

---

## Repository Layout

```
src/swhforest/        the Python package (index, ast-grep compiler, features, blob store)
  langspecs/          per-language Tree-sitter node mappings (functions, classes, docstrings)
examples/             runnable examples 01-04 (see Quick Start)
tests/                property and unit tests of the structural index
scripts/release/      build the ten-language release from Stack-Edu + Software Heritage
scripts/sample/       build the 100k sample in catalog/
catalog/              docs + small JSON files of the 100k sample (Parquet on the data host)
SCHEMA.md             column-level documentation of every released table
```

Run every script from the repository root with `PYTHONPATH=src`.

---

## Using SWHForest: The Three Core Workflows

### 1. Structural Pattern Search (`swhforest.search` + `ast-grep`)

A `Pattern(type, text, fields, contains)` specifies AST node types, grammar field roles, exact leaf literals, and ancestor–descendant containment (`contains` at any depth). `swhforest.search()` extracts the mandatory AST features, intersects the sharded posting lists (`structure-<k>.parquet`), and compiles the pattern to an equivalent `ast-grep` rule:

- **Single-block (`exact=True`) patterns:** when a pattern consists of a single indexed building block—such as one containment pair (`except { pass }`) or one literal in a syntactic role (`$X.forName(..)`)—the index posting list is already exact (`0` blobs downloaded).
- **Multi-block patterns:** SWHForest prunes the corpus locally with zero false negatives, streams only the surviving candidate blobs from Software Heritage via `BlobStore`, and verifies them with `ast-grep`.

```python
import swhforest
from swhforest import Pattern

release_dir = "data/release/rust"

# --- Case A: Single-block [exact] query (answered locally, 0 files fetched) ---
# Rust functions enclosing an `unsafe` block at any depth: fn ..() { .. unsafe { .. } }
p_exact = Pattern(type="function_item", contains=Pattern(type="unsafe_block"))
res_exact = swhforest.search(release_dir, p_exact, language="rust")
print(f"Exact={res_exact['exact']} | Matching blobs: {res_exact['n_candidates']:,} (0 fetched)")

# --- Case B: Multi-block query (index pruning -> SWH fetch -> ast-grep verification) ---
# Rust `for` loop containing a `.clone()` method call: for .. in .. { .. .clone() }
call_clone = Pattern(
    type="call_expression",
    fields={"function": Pattern(
        type="field_expression",
        fields={"field": Pattern(type="field_identifier", text="clone")}
    )}
)
p_multi = Pattern(type="for_expression", contains=call_clone)

res_multi = swhforest.search(release_dir, p_multi, language="rust", verify_limit=10)
print(f"Exact={res_multi['exact']} | Candidates after index pruning: {res_multi['n_candidates']:,}")
print(f"Compiled ast-grep rule: {res_multi['ast_grep_rule']}")
print(f"Verified matches (first 10 candidates): {len(res_multi['verified_blob_ids'])}")
```

---

### 2. Boilerplate Detection & Benchmark Contamination Screening (`swhforest.find_clones`)

`swhforest.find_clones()` parses any target snippet or benchmark solution with Tree-sitter, computes the 64-bit bottom-up Merkle `shape` of its definitions (stripping identifier names and literal values), and locates all structurally identical definitions across the corpus. Using `swhforest.slice_definition()`, you can extract the matching function directly from its remote SWH blob via its `[start_byte, end_byte]` span without re-parsing the file:

```python
import swhforest

# Screen a benchmark solution (e.g., HumanEval/0) against all 43.4M Python definitions
benchmark_code = """
def has_close_elements(numbers, threshold):
    for idx, elem in enumerate(numbers):
        for idx2, elem2 in enumerate(numbers):
            if idx != idx2:
                distance = abs(elem - elem2)
                if distance < threshold:
                    return True
    return False
"""

matches = swhforest.find_clones(
    "data/release/python",
    benchmark_code,
    language="python",
    min_nodes=20,
    limit=5,
)

for m in matches:
    print(f"Match in {m['repo']}:{m['path']} (blob {m['blob_id']} [{m['start_byte']}:{m['end_byte']}])")
    # Fetch and slice only the matching function bytes from Software Heritage
    snippet = swhforest.slice_definition(m["blob_id"], m["start_byte"], m["end_byte"])
    print(snippet)
```

---

### 3. Syntactic Corpus Curation & Reproducible Views (`swhforest.curate_view`)

`swhforest.curate_view()` filters a language release by its syntactic metrics (e.g., file size, docstring presence, number of functions/methods/classes, AST depth, distinct shapes, or parse errors) and exports a reproducible **View Manifest** (`JSON`) containing the predicate, sorted `blob_id` list, and SHA-256 checksum:

```python
import swhforest

# Select documented Python files > 10 kB with at least 5 functions/methods
view = swhforest.curate_view(
    "data/release/python",
    where="bytes > 10000 AND has_docstring = true AND (n_functions + n_methods) >= 5",
    out_manifest="curated_python_view.json",
)
print(f"Curated {view['n_blobs']:,} blobs ({view['total_bytes'] / 1e9:.2f} GB)")
print(f"View SHA-256: {view['blob_ids_sha256']}")

# Verify that a release directory matches its published view_release.json checksum
assert swhforest.verify_view("data/release/rust", "data/release/rust/view_release.json")
```

> **Open-Format Interoperability:** Because SWHForest stores its tables in standard Apache Parquet (`rows.parquet`, `catalog.parquet`, `functions.parquet`, `shapes.parquet`, `structure-<k>.parquet`; see [`SCHEMA.md`](SCHEMA.md)), you can also join or aggregate them directly from DuckDB, Polars, or Spark alongside `swhforest`.

---

## Dataset Architecture & Release Summary (v0.2.0)

Each language directory (`data/release/<language>/`) contains five Parquet tables linked by `blob_id` (and compact integer `row` ID) plus its reproducible view manifest:

| File | Granularity | Contents |
| :--- | :--- | :--- |
| `rows.parquet` | 1 row / unique `blob_id` | Compact integer file ID (`row`), `blob_id`, and one representative upstream occurrence (`repo`, `path`, `license_type`, `detected_licenses`, `edu_score`). |
| `catalog.parquet` | 1 row / file (`blob_id`) | Upstream metadata joined with **17 syntactic metrics**: size (`bytes`, `lines`), AST structure (`ast_nodes`, `ast_leaves`, `ast_depth`, `parse_error`), documentation (`comment_nodes`, `has_docstring`, `comment_ratio`), and code elements (`n_defs`, `n_functions`, `n_methods`, `n_classes`, `n_distinct_shapes`, `n_imports`, `n_string_literals`, `n_identifiers`). |
| `functions.parquet` | 1 row / definition | Every `function`, `method`, and `class` (`340.6M` total) with `blob_id`, byte span (`start_byte`, `end_byte`), `nodes`, `has_docstring`, and 64-bit Merkle `shape`. |
| `shapes.parquet` | 1 row / `(language, kind, shape)` | Groups definitions sharing an identical `shape`, recording cluster `count`, `mean_nodes`, and a sample `blob_id[start:end]`. |
| `structure-<k>.parquet` | 2M-file index shards | Inverted structural index mapping 64-bit AST building blocks (`E`, `T`, `A`) to gap-encoded sorted lists of file IDs (`row`). |
| `view_release.json` | 1 manifest / language | Reproducible view manifest recording the selection predicate, pinned parser versions, `n_blobs`, and SHA-256 checksum of the sorted `blob_id` list. |

### Ten-Language Stack-Edu Release (40% Deterministic SHA-1 Sample)

SWHForest v0.2.0 covers the ten largest languages of [`HuggingFaceTB/stack-edu`](https://huggingface.co/datasets/HuggingFaceTB/stack-edu) (revision `eeec5ca`). Among deduplicated files of 200 B–200 kB, it selects all blobs whose `blob_id` starts below `66666666` (a deterministic, seed-free **40% uniform sample** of the SHA-1 hash space that subsumes smaller prefix samples such as 10% below `19999999`).

| Language | Eligible (200 B–200 kB) | Ingested Files | Missing | Repos | Source Text (GB) | Definitions | Documented Files | Tables (GB) | Index (GB) |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Java** | 43,574,410 | 17,429,408 | 0 | 4,478,245 | 53.94 | 135,578,481 | 7,194,971 | 6.15 | 7.52 |
| **Python** | 23,700,120 | 9,481,411 | 53 | 3,423,748 | 27.10 | 43,406,601 | 2,147,784 | 2.82 | 3.16 |
| **C++** | 16,139,941 | 6,453,978 | 12 | 1,370,702 | 17.31 | 39,997,408 | 984,358 | 2.25 | 2.71 |
| **JavaScript** | 12,937,239 | 5,173,711 | 3 | 3,199,444 | 13.77 | 39,520,638 | 685,552 | 2.04 | 1.77 |
| **C#** | 11,403,979 | 4,560,555 | 3 | 1,368,622 | 13.74 | 30,471,416 | 1,087,850 | 1.65 | 1.81 |
| **PHP** | 9,763,473 | 3,904,782 | 10 | 1,245,428 | 11.24 | 20,813,360 | 1,949,731 | 1.17 | 0.86 |
| **C** | 5,663,818 | 2,266,959 | 3 | 677,872 | 6.21 | 8,370,541 | 349,608 | 0.64 | 0.74 |
| **TypeScript** | 4,185,146 | 1,673,249 | 0 | 608,053 | 3.85 | 12,517,615 | 399,712 | 0.66 | 0.66 |
| **Go** | 1,882,194 | 752,530 | 0 | 321,198 | 1.84 | 4,823,599 | — | 0.26 | 0.26 |
| **Rust** | 1,122,861 | 448,639 | 0 | 134,221 | 2.12 | 5,149,143 | 155,413 | 0.23 | 0.29 |
| **Total** | **130,373,181** | **52,145,222** | **84** | — | **151.12** | **340,648,802** | **14,954,979** | **17.87** | **19.77** |

---

## Rebuilding the Dataset from Upstream

To run the ingestion pipeline on any language or custom corpus:

```bash
# 0. Download upstream Stack-Edu metadata shards (pointers only, no code)
PYTHONPATH=src python scripts/release/download_stackedu_meta.py

# 1. Select the 40% SHA-1 prefix sample (writes data/release/<lang>/rows.parquet)
PYTHONPATH=src python scripts/release/select_release.py Rust 0.4

# 2. Stream blobs from SWH, parse with Tree-sitter, write Parquet tables + index shards
PYTHONPATH=src python scripts/release/ingest_stream.py rust 32 8

# 3. Compute release statistics and view_release.json manifests
PYTHONPATH=src python scripts/release/release_stats.py
```

### Pinned Parser Versions

All tables, `shape` hashes, and structural index shards were built with:
- `tree-sitter==0.26.0`
- `tree-sitter-python==0.25.0`, `tree-sitter-javascript==0.25.0`, `tree-sitter-java==0.23.5`, `tree-sitter-typescript==0.23.2`, `tree-sitter-c==0.24.2`, `tree-sitter-cpp==0.23.4`, `tree-sitter-c-sharp==0.23.5`, `tree-sitter-go==0.25.0`, `tree-sitter-rust==0.24.2`, `tree-sitter-php==0.25.1`.

---

## Licensing, Governance, and Citation

* **Code License:** MIT (see [`LICENSE`](LICENSE)).
* **Data License (Parquet metadata, metrics, shape hashes, and structural index):** CC-BY-4.0.
* **Underlying Source Files:** **No source text is distributed by SWHForest.** Each referenced file remains in the Software Heritage Archive under its original upstream license; users must honor the upstream terms when fetching blobs.
* **Opt-Out and Takedowns:** SWHForest follows upstream removals from The Stack v2 / Stack-Edu and Software Heritage. Withdrawn `blob_id`s are dropped in subsequent releases.

```bibtex
@inproceedings{pennino2026swhforest,
  author    = {Pennino, Federico and Gurioli, Andrea and Gabbrielli, Maurizio and Ferragina, Paolo},
  title     = {{SWHForest}: A Structural Curation and Search Layer for Large-Scale Code Corpora},
  booktitle = {Proceedings of the International Conference on Mining Software Repositories (MSR)},
  year      = {2026}
}
```
