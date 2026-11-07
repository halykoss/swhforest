"""Example 2: Structural boilerplate detection via shape clusters.

Inspects `shapes.parquet` to identify ubiquitous AST shapes (e.g. repeated
class templates, common utility wrappers) occurring > 100 times.

A shape cluster in `shapes.parquet` is one (language, kind, shape) triple. The
`no_boilerplate` view instead groups `functions.parquet` by shape
hash alone (any language or kind); both counts are printed.

Expected output (100k build):
  === Example 2: Boilerplate Inspection via Shape Clusters ===
  Total shape clusters:        466,609
  Boilerplate clusters (>100): 159
  Boilerplate definitions:     42,621
  Shape hashes in >100 definitions (no_boilerplate view): 170 hashes, 45,975 definitions, 19,547 blobs

  Top 5 boilerplate structural shapes:
    [python method] shape=3925f0c589c70ebe count=2,508 nodes=15.0 (sample: 000ed0e241097be0459d602cd9dc5437dac9bb52[331:375])
    [python method] shape=92f34c1960dd26c3 count=1,613 nodes=19.0 (sample: 000fb595f1eb0d755a3b12684619ecaa43bdc87f[2588:2637])
    [javascript function] shape=1d3a59808ea99521 count=1,261 nodes=8.0 (sample: 000ac6b50d3074df73cc073f1e69d322b0595ef3[170:193])
    [javascript function] shape=2ae00bac842e02a4 count=966 nodes=11.0 (sample: 00bf3d138d249a2fa2181a75fb78a909bfaffca5[129:156])
    [javascript function] shape=de67795f4b894a92 count=921 nodes=20.0 (sample: 0038d1d05e8c2fd9367aaee3ee79e69dfc4e6f5c[5052:5114])
"""

import duckdb

def main():
    print("=== Example 2: Boilerplate Inspection via Shape Clusters ===")
    con = duckdb.connect()

    summary = con.execute("""
        SELECT count(*) AS total_clusters,
               count(*) FILTER (WHERE count > 100) AS boilerplate_clusters,
               coalesce(sum(count) FILTER (WHERE count > 100), 0) AS boilerplate_definitions
        FROM 'catalog/shapes.parquet'
    """).fetchone()

    print(f"Total shape clusters:        {summary[0]:,}")
    print(f"Boilerplate clusters (>100): {summary[1]:,}")
    print(f"Boilerplate definitions:     {summary[2]:,}")

    by_hash = con.execute("""
        WITH g AS (SELECT shape, count(*) AS n FROM 'catalog/functions.parquet'
                   GROUP BY shape HAVING count(*) > 100)
        SELECT (SELECT count(*) FROM g), (SELECT sum(n) FROM g),
               (SELECT count(DISTINCT blob_id) FROM 'catalog/functions.parquet'
                WHERE shape IN (SELECT shape FROM g))
    """).fetchone()
    print(f"Shape hashes in >100 definitions (no_boilerplate view): "
          f"{by_hash[0]:,} hashes, {by_hash[1]:,} definitions, {by_hash[2]:,} blobs")

    top_shapes = con.execute("""
        SELECT language, kind, shape_id, count, round(mean_nodes, 1) as mean_nodes,
               sample_blob, sample_start, sample_end
        FROM 'catalog/shapes.parquet'
        ORDER BY count DESC
        LIMIT 5
    """).fetchall()

    print("\nTop 5 boilerplate structural shapes:")
    for s in top_shapes:
        print(f"  [{s[0]} {s[1]}] shape={s[2]} count={s[3]:,} nodes={s[4]} (sample: {s[5]}[{s[6]}:{s[7]}])")

if __name__ == "__main__":
    main()
