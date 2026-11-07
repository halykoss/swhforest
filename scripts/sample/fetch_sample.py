"""Fetch the sampled blobs from Software Heritage into the local cache."""

import json
import sys

from swhforest.blobstore import BlobStore

SAMPLE = sys.argv[1] if len(sys.argv) > 1 else "data/sample_100k.jsonl"
CACHE = sys.argv[2] if len(sys.argv) > 2 else "data/blobs"
OUT = sys.argv[3] if len(sys.argv) > 3 else None

rows = [json.loads(line) for line in open(SAMPLE)]
print(f"{len(rows):,} blobs to fetch", flush=True)
store = BlobStore(CACHE, workers=48)
ok = store.ensure_local([r["blob_id"] for r in rows])
avail = set(ok)
kept = [r for r in rows if r["blob_id"] in avail]
print(f"available {len(kept):,} / {len(rows):,}, missing {len(rows) - len(kept):,}",
      flush=True)
if OUT:
    with open(OUT, "w") as f:
        for r in kept:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {OUT}", flush=True)
