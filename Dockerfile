# SWHForest image (release 0.2.0): the swhforest package, the examples, the tests, the
# 100k sample (catalog/, used by examples 01-03) and ONE language of the release,
# data/release/rust (the smallest).  It contains no source text: the blob cache
# (data/blobs), the Stack-Edu metadata (data/raw) and the other release languages are
# excluded (see .dockerignore).
#
#   docker build -t swhforest .
#   docker run --rm swhforest                                   # example 04 on data/release/rust, no download
#   docker run --rm swhforest python examples/01_curate_views.py
#   docker run --rm swhforest python examples/04_query_release.py data/release/rust --fetch 5   # fetches from SWH
#   docker run --rm swhforest python -m pytest -q tests/test_structindex.py
#
# Loading the Rust index takes a few GB of memory (all postings are decoded).
FROM python:3.12-slim-bookworm

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md SCHEMA.md LICENSE CITATION.cff ./
COPY src/ ./src/

# requirements.txt pins the Python and JavaScript grammars; the other eight grammars are
# pinned here at the versions recorded in data/release/*/view_release.json.
RUN pip install --no-cache-dir -r requirements.txt pytest && \
    pip install --no-cache-dir \
        tree-sitter-java==0.23.5 tree-sitter-typescript==0.23.2 tree-sitter-c==0.24.2 \
        tree-sitter-cpp==0.23.4 tree-sitter-c-sharp==0.23.5 tree-sitter-go==0.25.0 \
        tree-sitter-rust==0.24.2 tree-sitter-php==0.25.1 && \
    pip install --no-cache-dir -e .

# release sample: data/release/rust (explicit list: no _pairs/ scratch directory)
COPY data/release/rust/rows.parquet data/release/rust/catalog.parquet \
     data/release/rust/functions.parquet data/release/rust/shapes.parquet \
     data/release/rust/structure-000.parquet \
     data/release/rust/selection.json data/release/rust/ingest_metrics.json \
     data/release/rust/view_release.json data/release/rust/missing.txt \
     ./data/release/rust/

# 100k sample (explicit list: nothing else from catalog/)
COPY catalog/README.md catalog/SCHEMA.md \
     catalog/catalog.parquet catalog/functions.parquet catalog/shapes.parquet \
     catalog/structure.parquet \
     catalog/manifest.json catalog/metrics.json catalog/structure_metrics.json catalog/views.json \
     ./catalog/
COPY catalog/views/ ./catalog/views/
COPY examples/ ./examples/
COPY tests/ ./tests/

ENV PYTHONPATH=/workspace/src
CMD ["python", "examples/04_query_release.py", "data/release/rust"]
