# 0003 — Consumers read a published serving copy of the warehouse

- **Status:** accepted
- **Date:** 2026-10-28

## Context
DuckDB permits either one read-write process or several read-only processes per
database file. The API, the dashboard and the AI layer run all the time; dbt
writes once a day. Sharing one file would make either the pipeline or the API fail
with a lock error.

## Decision
dbt writes `warehouse/europulse.duckdb`. After a green `dbt build`, a publish step
checkpoints it and atomically replaces `warehouse/europulse_serving.duckdb`
(`os.replace`). Consumers open only the serving copy, read-only, with short-lived
connections (`common.warehouse.read_only_connection`).

## Alternatives considered
- **Single file with retries**: fragile, and a failed build could expose half-built tables.
- **A server database (Postgres)** for serving: one more service to run.
- **Export gold to Parquet** and query that: workable, but loses DuckDB constraints
  and makes the AI schema context harder to describe.

## Consequences
- Consumers only ever see the result of a fully successful build (a blue/green swap).
- The serving copy is a contract boundary: if it is not published, nothing changes downstream.
- On Windows a swap can wait briefly while a reader holds the file (handled with retries).
