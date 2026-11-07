"""Reservoir-sample 100k stack-edu blobs (50k Python + 50k JavaScript).

Reproducible: fixed seed, size filter, unique blob ids. Writes
data/sample_100k.jsonl plus a sampling manifest.
"""

from __future__ import annotations

import json
import random
import sys

import pyarrow.compute as pc
import pyarrow.parquet as pq

PY = "data/raw/py-train-00000.parquet"
JS = "data/raw/js-train-00000.parquet"
OUT = "data/sample_100k.jsonl"
META = "data/sample_100k.meta.json"

N_PER_LANG = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
SEED = 20261025


def sample(path: str, language: str, n: int, rng: random.Random):
    pf = pq.ParquetFile(path)
    res: list[dict] = []
    seen = 0
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(
            rg, columns=["blob_id", "repo_name", "path", "length_bytes", "language"]
        )
        mask = pc.and_(
            pc.greater_equal(t["length_bytes"], 200),
            pc.less_equal(t["length_bytes"], 200_000),
        )
        t = t.filter(mask)
        cols = {c: t[c].to_pylist() for c in t.column_names}
        for i in range(t.num_rows):
            seen += 1
            row = {
                "blob_id": cols["blob_id"][i],
                "repo": cols["repo_name"][i],
                "path": cols["path"][i],
                "length_bytes": cols["length_bytes"][i],
                "language": language,
            }
            if len(res) < n:
                res.append(row)
            else:
                j = rng.randrange(seen)
                if j < n:
                    res[j] = row
        if rg % 500 == 0:
            print(f"  {language}: {rg}/{pf.metadata.num_row_groups} row groups, "
                  f"{seen:,} eligible, reservoir {len(res):,}", flush=True)
    # dedupe by blob_id (same content can appear under several repos)
    uniq, seen_ids = [], set()
    for r in res:
        if r["blob_id"] not in seen_ids:
            seen_ids.add(r["blob_id"])
            uniq.append(r)
    print(f"{language}: {len(uniq):,} unique blobs from {seen:,} eligible", flush=True)
    return uniq, seen


def main() -> None:
    rng = random.Random(SEED)
    py, py_seen = sample(PY, "python", N_PER_LANG, rng)
    js, js_seen = sample(JS, "javascript", N_PER_LANG, rng)
    rows = py + js
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    meta = {
        "seed": SEED,
        "n_per_language": N_PER_LANG,
        "blobs": len(rows),
        "languages": {"python": len(py), "javascript": len(js)},
        "eligible_rows": {"python": py_seen, "javascript": js_seen},
        "size_filter": [200, 200_000],
        "source": {
            "python": "stack-edu Python/train-00000-of-00005.parquet",
            "javascript": "stack-edu JavaScript/train-00000-of-00003.parquet",
        },
    }
    with open(META, "w") as f:
        json.dump(meta, f, indent=1)
    print(f"wrote {len(rows):,} rows to {OUT}")


if __name__ == "__main__":
    main()
