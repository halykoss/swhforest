"""Example 1: Curate views and avoid downloading unneeded blobs.

Reads `catalog.parquet` and applies curation filters to obtain a clean subset
of the corpus, measuring data transfer savings against full download.

Expected output (100k build):
  === Example 1: View Curation & Transfer Savings ===
  Full corpus:           100,000 blobs (276.8 MB text)
  Curated subset:        599 blobs (12.9 MB text, 354,971 lines)
  Data transfer avoided: 95.3%
  Size-only baseline:    2,337 blobs (44.1 MB text) -> catalog avoids 71% of it

  Sample curated blobs:
    8c17e1dda612a0d3c2a7aeb0ea0fef2e25918ee1 (166,031 bytes, 43 functions) -> leo-editor/leo-editor:/leo/core/leoAst.py
    da28fcb436882f92ce31abfe2f3144f38076d0db (142,787 bytes, 6 functions) -> basil08/domonic:/domonic/javascript.py
    8bd4bf820e7a13a34833b1c22420ed27d98ba121 (131,800 bytes, 7 functions) -> neutrons/CrystalPlan:/model/goniometer.py

"Data transfer avoided" is relative to downloading the whole build. The
size-only baseline is what the Stack-Edu table alone can select (Python rows with
length_bytes > 10 KB, i.e. catalog.source_bytes); the other two conditions need
the catalog.
"""

import duckdb
import pyarrow.parquet as pq

def main():
    print("=== Example 1: View Curation & Transfer Savings ===")
    con = duckdb.connect()

    # Query 1: Filter python blobs > 10 KB with docstrings and >= 5 functions
    res = con.execute("""
        SELECT count(*) AS n_blobs,
               coalesce(sum(bytes), 0) AS total_bytes,
               coalesce(sum(lines), 0) AS total_lines
        FROM 'catalog/catalog.parquet'
        WHERE language = 'python'
          AND bytes > 10000
          AND has_docstring = true
          AND n_functions >= 5
    """).fetchone()

    n_all, total_cor_bytes = con.execute(
        "SELECT count(*), sum(bytes) FROM 'catalog/catalog.parquet'").fetchone()
    n_blobs, total_bytes, total_lines = res
    avoided_pct = 100.0 * (1.0 - total_bytes / total_cor_bytes)

    print(f"Full corpus:           {n_all:,} blobs ({total_cor_bytes / 1e6:.1f} MB text)")
    print(f"Curated subset:        {n_blobs:,} blobs ({total_bytes / 1e6:.1f} MB text, {total_lines:,} lines)")
    print(f"Data transfer avoided: {avoided_pct:.1f}%")

    # Without SWHForest only the size condition can be evaluated before fetching
    n_pre, pre_bytes = con.execute("""
        SELECT count(*), coalesce(sum(bytes), 0)
        FROM 'catalog/catalog.parquet'
        WHERE language = 'python' AND source_bytes > 10000
    """).fetchone()
    print(f"Size-only baseline:    {n_pre:,} blobs ({pre_bytes / 1e6:.1f} MB text) "
          f"-> catalog avoids {100.0 * (1.0 - total_bytes / pre_bytes):.0f}% of it")

    # Sample top 3 curated blob IDs
    samples = con.execute("""
        SELECT blob_id, repo, path, bytes, n_functions
        FROM 'catalog/catalog.parquet'
        WHERE language = 'python'
          AND bytes > 10000
          AND has_docstring = true
          AND n_functions >= 5
        ORDER BY bytes DESC
        LIMIT 3
    """).fetchall()

    print("\nSample curated blobs:")
    for b in samples:
        print(f"  {b[0]} ({b[3]:,} bytes, {b[4]} functions) -> {b[1]}:{b[2]}")

if __name__ == "__main__":
    main()
