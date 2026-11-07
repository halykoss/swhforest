"""Write real view manifests: predicate + versions + checksum + blob_id list."""
import hashlib, json, os
import pyarrow.compute as pc
import pyarrow.parquet as pq

cat = pq.read_table("catalog/catalog.parquet")
func = pq.read_table("catalog/functions.parquet")
base = json.load(open("catalog/manifest.json"))
g = func.group_by("shape").aggregate([("blob_id", "count")])
common = g.filter(pc.greater(g["blob_id_count"], 100))["shape"]
boiler = set(pc.unique(func.filter(pc.is_in(func["shape"], value_set=common))["blob_id"]).to_pylist())
ids = cat["blob_id"].to_pylist()
doc = cat["has_docstring"].to_pylist()
size = cat["bytes"].to_pylist()
rows = list(zip(ids, doc, size))
defs = {
 "all": ("true", lambda r: True),
 "docstring": ("catalog.has_docstring = true", lambda r: r[1]),
 "no_boilerplate": ("blob has no definition whose shape hash (any language or kind) occurs in >100 rows of functions.parquet",
                    lambda r: r[0] not in boiler),
}
os.makedirs("catalog/views", exist_ok=True)
summary = {}
for name, (pred, f) in defs.items():
    sel = sorted((r for r in rows if f(r)), key=lambda r: r[0])
    bl = [r[0] for r in sel]
    chk = hashlib.sha256("\n".join(bl).encode()).hexdigest()
    m = {"view": name, "dataset_version": base.get("dataset_version"),
         "source_revision": base.get("source_revision"),
         "parser_versions": base.get("parser_versions"),
         "predicate": pred, "n_blobs": len(bl), "text_bytes": sum(r[2] for r in sel),
         "blob_ids_sha256": chk, "blobs": bl}
    p = f"catalog/views/{name}.json"
    json.dump(m, open(p, "w"))
    summary[name] = {"blobs": len(bl), "manifest_bytes": os.path.getsize(p),
                     "text_bytes": m["text_bytes"], "sha256": chk}
json.dump(summary, open("catalog/views.json", "w"), indent=1)
print(json.dumps(summary, indent=1))
