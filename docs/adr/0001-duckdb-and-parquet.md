# 0001 — DuckDB + Parquet as the local warehouse

- **Status:** accepted
- **Date:** 2026-10-28

## Context
The project must run for free on a laptop with one command, while using the same
concepts (lakehouse files + SQL engine + medallion layers) as cloud stacks such as
Azure Data Lake + Synapse/Fabric or Databricks. Data volume is small: ~5,600 rows
per daily run across three datasets.

## Decision
Store bronze as Parquet files on disk and use DuckDB (single file) as the SQL
engine for silver and gold, driven by dbt through the `dbt-duckdb` adapter.

## Alternatives considered
- **PostgreSQL**: a server to run and tune, row-oriented, and no native Parquet
  reading. Better for many concurrent writers, which we do not have.
- **SQLite**: no columnar storage, weak analytical SQL, no Parquet.
- **Cloud warehouse from day one**: costs money and adds credentials before
  there is anything to show (see the "start local" pitfall in the project plan).

## Consequences
- Zero servers, sub-second queries, Parquet read natively with `read_parquet`.
- DuckDB allows one writer per file, so consumers read a separate serving copy
  (ADR 0003).
- Moving to Azure means swapping storage paths (ADLS Gen2) and the dbt adapter;
  the medallion layout and models stay. Revisit if data reaches tens of GB or if
  several writers must work concurrently.
