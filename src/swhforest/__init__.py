"""SWHForest: A Structural Curation and Search Layer for Pointer-Based Code Corpora."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .query import Pattern

__version__ = "0.2.0"
__all__ = [
    "Pattern",
    "search",
    "find_clones",
    "slice_definition",
    "curate_view",
    "verify_view",
]


def _load_index(directory: str):
    from .structindex import ShardedIndex, StructIndex
    single = os.path.join(directory, "structure.parquet")
    if os.path.exists(single):
        return StructIndex.load(single)
    return ShardedIndex.load(directory)


def search(
    directory: str,
    pattern: Pattern,
    language: str,
    *,
    verify_limit: int = 0,
    cache_dir: str = "data/blobs",
) -> dict[str, Any]:
    """Search the structural AST index and optionally verify surviving candidates with ast-grep.

    - Single-block patterns (`exact=True`) are resolved directly from the index with 0 files fetched.
    - Multi-block patterns prune the corpus to candidate `blob_id`s and (when `verify_limit > 0`)
      fetch and verify candidates using the compiled `ast-grep` relational rule.
    """
    import pyarrow.parquet as pq
    from .astgrep import compile_pattern, count_matches
    from .blobstore import BlobStore
    from .structindex import is_exact

    idx = _load_index(directory)
    rows = idx.candidates(pattern, language)
    exact = is_exact(pattern, language)
    rule = compile_pattern(pattern, language)

    rows_pq = os.path.join(directory, "rows.parquet")
    id_source = rows_pq if os.path.exists(rows_pq) else os.path.join(directory, "catalog.parquet")
    id_col = pq.read_table(id_source, columns=["blob_id"])["blob_id"]

    cand_rows = [] if rows is None else [int(r) for r in rows]
    cand_ids = [id_col[r].as_py() for r in cand_rows]

    verified: list[str] | None = None
    if exact:
        verified = cand_ids
    elif verify_limit > 0 and rule is not None:
        store = BlobStore(cache_dir)
        to_check = cand_ids[:verify_limit]
        store.ensure_local(to_check)
        verified = []
        for bid in to_check:
            if store.has(bid):
                src = store.reopen(bid)
                if count_matches(src, language, rule) > 0:
                    verified.append(bid)

    return {
        "exact": exact,
        "n_candidates": len(cand_ids),
        "candidate_blob_ids": cand_ids,
        "ast_grep_rule": rule,
        "verified_blob_ids": verified,
    }


def find_clones(
    directory: str,
    source_code: bytes | str,
    language: str = "python",
    *,
    min_nodes: int = 1,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Compute the 64-bit AST Merkle `shape` of definitions in `source_code` and find matches."""
    import duckdb
    from .features import analyze
    from .grammars import make_parser

    src_bytes = source_code.encode("utf-8") if isinstance(source_code, str) else source_code
    parser = make_parser(language)
    _, defs = analyze(src_bytes, language, parser)
    target_defs = [d for d in defs if d["nodes"] >= min_nodes]
    if not target_defs:
        return []

    fn_pq = os.path.join(directory, "functions.parquet")
    cat_pq = os.path.join(directory, "catalog.parquet")
    con = duckdb.connect()
    results: list[dict[str, Any]] = []
    for d in target_defs:
        shape_val = int(d["shape"])
        rows = con.execute(
            f"""
            SELECT f.blob_id, c.repo, c.path, f.kind, f.start_byte, f.end_byte, f.nodes
            FROM read_parquet('{fn_pq}') f
            JOIN read_parquet('{cat_pq}') c USING (blob_id)
            WHERE f.shape = ?
            LIMIT ?
            """,
            [shape_val, limit],
        ).fetchall()
        for r in rows:
            results.append({
                "shape": shape_val,
                "blob_id": r[0],
                "repo": r[1],
                "path": r[2],
                "kind": r[3],
                "start_byte": r[4],
                "end_byte": r[5],
                "nodes": r[6],
            })
    return results


def slice_definition(
    blob_id: str,
    start_byte: int,
    end_byte: int,
    *,
    cache_dir: str = "data/blobs",
) -> str:
    """Fetch `blob_id` from Software Heritage and slice `[start_byte:end_byte]` without re-parsing."""
    from .blobstore import BlobStore

    store = BlobStore(cache_dir)
    store.ensure_local([blob_id])
    raw = store.reopen(blob_id)
    return raw[start_byte:end_byte].decode("utf-8", errors="replace")


def curate_view(
    directory: str,
    where: str,
    *,
    out_manifest: str | None = None,
) -> dict[str, Any]:
    """Filter `catalog.parquet` by a syntactic predicate and build a reproducible view manifest."""
    import duckdb

    cat_pq = os.path.join(directory, "catalog.parquet")
    con = duckdb.connect()
    rows = con.execute(
        f"""
        SELECT blob_id, bytes
        FROM read_parquet('{cat_pq}')
        WHERE {where}
        ORDER BY blob_id
        """
    ).fetchall()
    blob_ids = [r[0] for r in rows]
    total_bytes = sum(int(r[1] or 0) for r in rows)
    checksum = hashlib.sha256("\n".join(blob_ids).encode("utf-8")).hexdigest()

    manifest = {
        "swhforest_version": __version__,
        "predicate": where,
        "n_blobs": len(blob_ids),
        "total_bytes": total_bytes,
        "blob_ids_sha256": checksum,
    }
    if out_manifest is not None:
        with open(out_manifest, "w", encoding="utf-8") as f:
            json.dump({**manifest, "blob_ids": blob_ids}, f, indent=2)
    return {**manifest, "blob_ids": blob_ids}


def verify_view(directory: str, manifest_path: str) -> bool:
    """Verify that the sorted `blob_id`s in `directory/catalog.parquet` match `manifest_path`."""
    import pyarrow.parquet as pq

    with open(manifest_path, "r", encoding="utf-8") as f:
        view = json.load(f)
    cat_pq = os.path.join(directory, "catalog.parquet")
    ids = sorted(pq.read_table(cat_pq, columns=["blob_id"])["blob_id"].to_pylist())
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return digest == view["blob_ids_sha256"]
