"""Download the Stack-Edu metadata shards (no source text) for the release languages."""
import json
import os
import urllib.request

REV = "eeec5caac5cc3758a18f1d3ba4416837a9ba814c"
LANGS = ["Python", "JavaScript", "Java", "Cpp", "CSharp", "PHP", "C", "TypeScript", "Go", "Rust"]
for L in LANGS:
    tree = json.load(urllib.request.urlopen(f"https://huggingface.co/api/datasets/HuggingFaceTB/stack-edu/tree/{REV}/{L}"))
    os.makedirs(f"data/raw/stack-edu/{L}", exist_ok=True)
    for f in tree:
        dst = f"data/raw/stack-edu/{f['path']}"
        if os.path.exists(dst) and os.path.getsize(dst) == f["size"]:
            continue
        url = f"https://huggingface.co/datasets/HuggingFaceTB/stack-edu/resolve/{REV}/{f['path']}"
        urllib.request.urlretrieve(url, dst + ".part")
        os.replace(dst + ".part", dst)
        print(dst, f["size"], flush=True)
print("done", flush=True)
