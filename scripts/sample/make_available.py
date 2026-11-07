import json, os
rows=[json.loads(l) for l in open("data/sample_100k.jsonl")]
kept=[r for r in rows if os.path.exists(f"data/blobs/{r['blob_id']}.gz")]
with open("data/sample_available.jsonl","w") as f:
    for r in kept: f.write(json.dumps(r)+"\n")
print(len(kept), "of", len(rows))
