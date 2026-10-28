# 0005 — Immutable bronze snapshots to keep revision history

- **Status:** accepted
- **Date:** 2026-10-28

## Context
Eurostat does not version its data: the API only serves the latest value of each
observation. Statistics are revised (e.g. a provisional inflation figure is
corrected a month later), and those revisions are lost unless someone keeps them.

## Decision
Every ingestion run writes a new Parquet file per dataset under
`dataset=<code>/ingestion_date=<date>/<run_id>.parquet` and never overwrites or
deletes. Silver reads all snapshots; `int_observations_latest` picks the newest
value per observation; `fct_data_revisions` lists every value that changed between
runs; a dbt snapshot (`snap_observations`) records validity intervals.

## Alternatives considered
- **Overwrite latest file**: simplest, but revision history is gone forever.
- **Only write when content changed** (hash check): smaller, but loses the proof
  that a run happened and saw identical data; storage is negligible (~100 KB/day).

## Consequences
- Re-running is safe (idempotent at the gold layer): duplicates collapse to the
  latest run in silver.
- Storage grows linearly (~35 MB/year at current volume). A retention/compaction
  job would be the next step at larger scale.
