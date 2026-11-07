"""Select the release rows of one Stack-Edu language: size filter, dedup, 40% hash sample.

A blob is selected when the first 8 hex digits of its sha1_git are below 0x66666666,
i.e. int(prefix, 16) < 0.4 * 2**32.  SHA-1 is uniform, so the selection is a uniform
random sample without a seed, and samples at smaller fractions are nested in it.
Usage: python scripts/release/select_release.py <StackEduConfig> [fraction]  ->  data/release/<lang>/rows.parquet
"""
import json
import os
import sys

import duckdb

from swhforest.grammars import STACK_EDU

CFG = sys.argv[1]
FRAC = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
LANG = STACK_EDU[CFG]
THRESH = f"{int(FRAC * 2**32):08x}"
src = f"data/raw/stack-edu/{CFG}/*.parquet"
out = f"data/release/{LANG}"
os.makedirs(out, exist_ok=True)
con = duckdb.connect()
total = con.execute(f"select count(*) from read_parquet('{src}')").fetchall()[0][0]
in_size = con.execute(f"select count(*) from read_parquet('{src}') where length_bytes between 200 and 200000").fetchall()[0][0]
con.execute(f"""
copy (
  select blob_id, any_value(repo_name) as repo, any_value(path) as path, '{LANG}' as language,
         any_value(length_bytes) as length_bytes, any_value(src_encoding) as src_encoding,
         any_value(score) as edu_score, any_value(int_score) as edu_int_score,
         any_value(detected_licenses) as detected_licenses, any_value(license_type) as license_type
  from read_parquet('{src}')
  where length_bytes between 200 and 200000 and substr(blob_id, 1, 8) < '{THRESH}'
  group by blob_id
  order by blob_id
) to '{out}/rows.parquet' (format parquet, compression zstd)
""")
sel = con.execute(f"select count(*), sum(length_bytes), count(distinct repo) from read_parquet('{out}/rows.parquet')").fetchall()[0]
m = {"config": CFG, "language": LANG, "fraction": FRAC, "hash_threshold_hex": THRESH,
     "rows_total": total, "rows_200B_200kB": in_size, "selected_blobs": sel[0],
     "selected_bytes": int(sel[1] or 0), "selected_repos": sel[2],
     "source_revision": "eeec5caac5cc3758a18f1d3ba4416837a9ba814c"}
json.dump(m, open(f"{out}/selection.json", "w"), indent=1)
print(json.dumps(m))
