# Schema

The column-level documentation of SWHForest (release 0.2.0, `data/release/<language>/`)
is in [`../SCHEMA.md`](../SCHEMA.md).

This directory, `catalog/`, is the 100k-blob Python + JavaScript build of dataset
version 0.1.1. It is kept as a **small sample for the examples** and is not part of release 0.2.0;
its layout (single `structure.parquet`, row number = position in `catalog.parquet`,
`manifest.json`, `views/`) is described in the
[appendix of `SCHEMA.md`](../SCHEMA.md#appendix-the-100k-sample-catalog).
