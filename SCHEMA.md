# SWHForest schema (dataset version 0.2.0)

SWHForest describes the syntax of code blobs selected from `HuggingFaceTB/stack-edu`
(revision `eeec5caac5cc3758a18f1d3ba4416837a9ba814c`) without redistributing them.
Every table is keyed by the Software Heritage `sha1_git` of the blob (`blob_id`);
`swh:1:cnt:<blob_id>` is its SWHID, and the text can be fetched from
`https://softwareheritage.s3.amazonaws.com/content/<blob_id>` (gzip).

The release covers ten Stack-Edu languages: `python`, `javascript`, `java`, `cpp`,
`csharp`, `php`, `c`, `typescript`, `go`, `rust`. Each language is built
independently into `data/release/<language>/`. Counts, sizes and checksums per
language are in the README and in the JSON files of each directory; this file
documents the columns.

The earlier 100k-blob Python + JavaScript build `catalog/` is **not part of release
0.2.0**: it is kept as a small sample for the examples (see the
[appendix](#appendix-the-100k-sample-catalog)).

All node counts include anonymous tree-sitter nodes (keywords, punctuation) unless
stated otherwise.

---

## 0. Files of `data/release/<language>/`

| File | One row per | Order | Written by |
|---|---|---|---|
| `rows.parquet` | selected Stack-Edu blob | `blob_id` | `scripts/release/select_release.py` |
| `catalog.parquet` | ingested blob | `row` | `scripts/release/ingest_stream.py` |
| `functions.parquet` | definition (function, method, class) | unspecified | `scripts/release/ingest_stream.py` |
| `shapes.parquet` | shape cluster (`language`, `kind`, `shape`) | `count` descending | `scripts/release/ingest_stream.py` |
| `structure-<k>.parquet` | structural-index feature of shard `k` | `feature` | `scripts/release/ingest_stream.py` |
| `selection.json` | selection counts and threshold | | `scripts/release/select_release.py` |
| `ingest_metrics.json` | build counts, timings, shard sizes, soundness check | | `scripts/release/ingest_stream.py` |
| `view_release.json` | view manifest of the language (checksum of its blob list) | | `scripts/release/release_stats.py` |
| `missing.txt` | `blob_id` not retrievable from SWH (one per line, may be empty) | | `scripts/release/ingest_stream.py` |

All Parquet files are zstd-compressed (`structure-<k>.parquet` with level 12). The
`_pairs/` directory is a build scratch directory (empty after a build) and is not
released.

### Row numbers

`rows.parquet` is sorted by `blob_id`; the **row number** of a blob is its 0-based
position in this file. The same number is the `row` column of `catalog.parquet` and the
value stored in the postings of the structural index. Blobs listed in `missing.txt`
keep their row number in `rows.parquet` but have no row in `catalog.parquet` and no
posting in the index, so `catalog.parquet` row `i` (file position) is in general **not**
row number `i`: join on the `row` column, or look the blob up in `rows.parquet` by
position.

---

## 1. `rows.parquet`: the selected Stack-Edu rows

The input of the build: Stack-Edu rows of the language with
`200 <= length_bytes <= 200000` and `substr(blob_id, 1, 8) < '66666666'`, one row per
distinct `blob_id` (`any_value` of the other columns over duplicates).

| Column | Type | Description |
|---|---|---|
| `blob_id` | `string` | 40-hex SWH `sha1_git` (sort key) |
| `repo` | `string` | Stack-Edu `repo_name` |
| `path` | `string` | Stack-Edu `path` |
| `language` | `string` | catalog language name (lower case) |
| `length_bytes` | `int64` | Stack-Edu `length_bytes` |
| `src_encoding` | `string` | Stack-Edu `src_encoding` |
| `edu_score` | `double` | Stack-Edu `score` |
| `edu_int_score` | `int64` | Stack-Edu `int_score` |
| `detected_licenses` | `list<string>` | Stack-Edu `detected_licenses` |
| `license_type` | `string` | Stack-Edu `license_type` |

**Selection.** The first 8 hex digits of a SHA-1 are uniform, so the condition
`prefix < 0x66666666` (= `int(0.4 * 2**32)`) is a uniform 40% sample without a seed.
It is reproducible from the identifiers alone, and a sample at a smaller fraction
(a smaller threshold) is nested in it. `selection.json` records `fraction` (0.4),
`hash_threshold_hex`, `rows_total` (all rows of the language), `rows_200B_200kB` (rows
within the size filter), `selected_blobs`, `selected_bytes` (sum of `length_bytes`),
`selected_repos` and `source_revision`.

---

## 2. `catalog.parquet`: one row per ingested blob

| Column | Type | Description |
|---|---|---|
| `row` | `int64` | Row number of the blob in `rows.parquet` and in the index (sort key) |
| `blob_id` | `string` | 40-hex SWH `sha1_git` |
| `repo` | `string` | Repository name, from Stack-Edu (`repo_name`) |
| `path` | `string` | File path within the repository, from Stack-Edu |
| `language` | `string` | One of the ten language names above |
| `bytes` | `int64` | Length in bytes of the content served by Software Heritage |
| `source_bytes` | `int64` | `length_bytes` of the Stack-Edu row |
| `lines` | `int64` | Number of `\n` in the blob + 1 |
| `src_encoding` | `string` | Source encoding reported by Stack-Edu |
| `license_type` | `string` | Stack-Edu license category |
| `detected_licenses` | `list<string>` | Licenses detected upstream (Stack-Edu / The Stack v2) |
| `edu_score` | `double` | Stack-Edu educational-quality score |
| `edu_int_score` | `int64` | Integer version of `edu_score` |
| `parse_error` | `bool` | The tree-sitter tree contains an error or missing node |
| `n_nodes` | `int64` | Number of nodes in the tree (named and anonymous) |
| `n_leaves` | `int64` | Number of nodes without children |
| `n_functions` | `int64` | Definitions of kind `function` (see "Extraction rules") |
| `n_methods` | `int64` | Definitions of kind `method` |
| `n_classes` | `int64` | Definitions of kind `class` |
| `n_comments` | `int64` | Number of comment nodes (the language's comment types) |
| `comment_bytes` | `int64` | Total bytes of the comment nodes |
| `comment_ratio` | `double` | `comment_bytes / max(bytes, 1)` |
| `max_depth` | `int64` | Maximum node depth (root = 0) |
| `mean_depth` | `double` | Mean node depth |
| `n_imports` | `int64` | Nodes of the language's import types |
| `n_strings` | `int64` | Nodes of the language's string-literal types |
| `n_identifiers` | `int64` | Nodes of the language's identifier types |
| `has_docstring` | `bool` | Python: at least one definition has a docstring. Other languages: the blob has a comment that starts with one of the language's doc prefixes |
| `docstring_count` | `int64` | Python: definitions with a docstring. Other languages: comments with a doc prefix |
| `shape_root` | `string` | 16-hex shape hash of the whole tree (section 3) |
| `n_defs` | `int64` | `n_functions + n_methods + n_classes` (= rows of this blob in `functions.parquet`) |
| `n_def_shapes` | `int64` | Number of distinct shape hashes among the blob's definitions |

`bytes` and `source_bytes` can differ because of line-ending normalization upstream;
byte offsets in `functions.parquet` refer to `bytes`, i.e. to the content SWH serves.
TypeScript files whose path ends in `.tsx` are parsed with the TSX grammar.

### Extraction rules per language

Which node types count as functions, methods, classes, comments, imports, strings and
identifiers, and which comment prefixes mark documentation, is data, not code:

* `python` and `javascript`: the `LANG` table at the top of `src/swhforest/features.py`
  (unchanged from the 100k sample; Python docstrings are string statements at the
  start of a body, JavaScript documentation is a `/**` comment);
* the eight other languages: one JSON spec each in `src/swhforest/langspecs/<language>.json`,
  with keys `functions`, `classes`, `methods`, `method_containers`, `comments`,
  `imports`, `strings`, `identifiers`, `docstring`, `doc_prefixes`, optional flags
  (`class_requires_body`, `qualified_methods` for C++) and a `_notes` field that
  explains the choices.

A function-like node is a `method` if it is listed in `methods` or has an ancestor in
`method_containers` (default: the `classes` types), otherwise a `function`. The Go
spec has no doc prefix, so `has_docstring` is always false for Go. The grammars of
every language and their Python modules are registered in `src/swhforest/grammars.py`;
the grammar versions used for the build are in `view_release.json`
(`parser_versions`).

---

## 3. `functions.parquet`: one row per definition

| Column | Type | Description |
|---|---|---|
| `blob_id` | `string` | Blob containing the definition |
| `language` | `string` | Language of the blob |
| `kind` | `string` | `function`, `method` or `class` |
| `start` | `int64` | Start byte offset in the blob (0-based) |
| `end` | `int64` | End byte offset (exclusive) |
| `nodes` | `int64` | Nodes in the definition's subtree |
| `shape` | `string` | 16-hex shape hash of the subtree |
| `has_docstring` | `bool` | Python: the body starts with a string expression. Always false for the other languages |
| `parse_error` | `bool` | `parse_error` of the blob |

**Shape hash.** A bottom-up Merkle hash with leaf text erased, using 8-byte BLAKE2b:
a leaf is `H("L" || type)`, an internal node is `H("I" || type || h(child_1) || ... ||
h(child_k))` over all children in order, named and anonymous. Up to hash collisions,
two subtrees have the same shape iff they are identical once identifier and literal
text is erased. Comments are nodes, so they are part of the shape. Shapes depend on
the grammar versions and are comparable only within a release and a language.

---

## 4. `shapes.parquet`: one row per shape cluster

A cluster is one (`language`, `kind`, `shape`) triple of `functions.parquet` of the
same language directory.

| Column | Type | Description |
|---|---|---|
| `language` | `string` | Language of the definitions |
| `kind` | `string` | `function`, `method` or `class` |
| `shape_id` | `string` | 16-hex shape hash |
| `count` | `int64` | Number of definitions in the cluster |
| `mean_nodes` | `double` | Mean `nodes` of the definitions |
| `sample_blob` | `string` | `blob_id` of the member with the smallest `start` |
| `sample_start` | `int64` | `start` of that member |
| `sample_end` | `int64` | `end` of that member |

Rows are sorted by `count`, descending.

---

## 5. `structure-<k>.parquet`: the structural index

One language's index is split into shards of 2,000,000 row numbers
(`SHARD_ROWS` in `scripts/release/ingest_stream.py`): shard `k` holds the postings of rows
`[2,000,000 k, 2,000,000 (k + 1))`. Row numbers are global within the language, so the
candidates of a pattern are the concatenation of the per-shard candidates. Load all
shards with

```python
from swhforest.structindex import ShardedIndex
idx = ShardedIndex.load("data/release/rust")      # every structure-*.parquet of the directory
rows = idx.candidates(pattern, "rust")             # numpy uint32 row numbers, or None
```

(`StructIndex.load(path)` loads a single shard.) Each shard file:

| Column | Type | Description |
|---|---|---|
| `feature` | `uint64` | 64-bit hash of the feature tuple: BLAKE2b with an 8-byte digest of its `repr`, read little-endian (`structindex.feature_hash`). Rows are sorted by this column |
| `family` | `string` | `E`, `T` or `A` (dictionary-encoded) |
| `df` | `uint32` | Number of blobs of the shard that contain the feature |
| `blobs` | `list<uint32>` | Row numbers, sorted and **gap-encoded**: the first value is a row number, each following value is the difference from the previous one. A prefix sum restores the rows |

Schema metadata (Parquet key-value metadata) of every shard:

| Key | Value |
|---|---|
| `languages` | the language name (one language per directory) |
| `blob_lang` | one byte per row of `rows.parquet` (all 0: the index of the language in `languages`) |
| `encoding` | `gap` |

### Features

| Family | Tuple | Meaning |
|---|---|---|
| `E` | `("E", parent type, field, child type)` | a named node has, in grammar field `field`, a named child that is not a leaf |
| `T` | `("T", parent type, field, leaf type, leaf text)` | a named node has, in field `field`, a named leaf with this text (a literal in its context) |
| `A` | `("A", ancestor type, descendant type)` | a named node has a named strict descendant of this type |

Features name **named** nodes only, but the extractor walks every node that the
matcher walks, including anonymous nodes that have children.

### Queries, soundness and exactness

A pattern (`swhforest.query.Pattern(type, text, fields, contains)`) is matched by
`swhforest.treematch` (`find_in_tree`; `contains` is satisfied by the node itself or any
descendant). `structindex.pattern_features(pattern, language)` compiles it into the
features that **every** match must contain; `candidates` intersects their postings,
rarest first. It returns a sorted array of row numbers that is a superset of the
matching blobs, an empty array if some required feature does not occur, or `None` if
the pattern implies no feature (every blob of the language is then a candidate).

* **Soundness.** A feature is emitted only if every match implies it, and only for node
  types that are named-only in every grammar of the language
  (`structindex.named_only_types`; for TypeScript both the TypeScript and TSX grammars).
  For `python` and `javascript` the build ran the exact matcher on every ingested blob
  for every built-in and Semgrep-derived query (`swhforest.queries`,
  `swhforest.semgrepqueries`) and recorded, per query, `matching_blobs`, `candidates`,
  `missed` and `exact` in `ingest_metrics.json` (`soundness`); the build fails if any
  query has `missed > 0`. The other eight languages have no recorded full-scan check.
* **Exactness.** `structindex.is_exact(pattern, language)` is true for single-relation
  patterns (`X` contains `Y`, an `A` feature; or `X.f` is the leaf `Y` with text `t`, a
  `T` feature). Their candidates are the answer and no blob needs to be fetched. Other
  candidates must be fetched, parsed (`swhforest.grammars.make_parser(language, path)`)
  and checked with `find_in_tree`, or with ast-grep: `swhforest.astgrep.compile_pattern`
  turns a pattern into an equivalent ast-grep rule (or `None` if it cannot be expressed
  exactly) and `count_matches` runs it.
* **Hash collisions** can only add candidates (pruning stays sound); for exact patterns
  they can add false positives. Exactness holds up to 64-bit hash collisions.
* **Privacy of literals.** Literal leaf texts enter the index only through the 64-bit
  hash of a `T` tuple. A hash can still be tested against a guessed literal.

---

## 6. JSON files

`selection.json`: see section 1.

`ingest_metrics.json`: `language`, `selected_blobs`, `ingested_blobs`, `missing_blobs`,
`ingest_seconds` (wall-clock), `blobs_per_second`, `parse_cpu_seconds`, `index_seconds`,
`processes`, `threads` (fetch threads per process), `shards` (per shard: `shard`,
`features`, `postings`, `bytes`), `sizes` (bytes of `catalog`, `functions`, `shapes`)
and, for `python` and `javascript`, `soundness` (section 5).

`view_release.json`: the selection as a citable view.

| Key | Description |
|---|---|
| `view` | `release_<language>` |
| `dataset_version` | `0.2.0` |
| `source_revision` | `{"repo": "HuggingFaceTB/stack-edu", "revision": "eeec5caac5cc3758a18f1d3ba4416837a9ba814c"}` |
| `predicate` | Text of the selection condition |
| `n_blobs` | Number of ingested blobs (rows of `catalog.parquet`) |
| `blob_ids_sha256` | SHA-256 (hex) of the ingested `blob_id`s, sorted, joined with `\n` (no trailing newline) |
| `parser_versions` | Versions of tree-sitter and of every grammar package |

Check it with
`hashlib.sha256("\n".join(sorted(catalog.blob_id)).encode()).hexdigest() == view["blob_ids_sha256"]`.

`results/release_stats.json` (written by `scripts/release/release_stats.py`, not part of a
language directory) collects per language the selection counts, catalog aggregates
(`blobs`, `repos`, `text_bytes`, `parse_errors`, `documented`, `definitions`, `kinds`,
boilerplate counts), index and table sizes, the soundness check and the view checksum.

---

## Appendix: the 100k sample (`catalog/`)

The 100k Python + JavaScript build (dataset version 0.1.1) is kept as a small sample
for the examples and for trying the tool; it is not part of release 0.2.0. Differences from the release layout:

* uniform reservoir samples with a seed from the first training shard of each
  language (see `manifest.json` of each build), not a hash selection;
* one directory for both languages: `catalog.parquet` has no `row` column, and the
  0-based **file position** in `catalog.parquet` is the row number used by the index;
* a single index file `structure.parquet` (load with `StructIndex.load`), whose
  `blob_lang` metadata maps each row to `javascript` (0) or `python` (1);
* `manifest.json`, `metrics.json` and `structure_metrics.json` instead of
  `selection.json` and `ingest_metrics.json`; `catalog/` also has `views/*.json` and
  `views.json` (view manifests with the same checksum rule as `view_release.json`, plus
  `text_bytes` and the `blobs` list);
* Snappy-compressed tables.

The column definitions of sections 2-4 apply, with `language` restricted to `python`
and `javascript`.
