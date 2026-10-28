# 0002 — Apache Airflow as the orchestrator

- **Status:** accepted
- **Date:** 2026-10-28

## Context
The pipeline needs a daily schedule (Eurostat publishes at 11:00 and 23:00 CET),
dependencies between steps, retries, failure alerts and a freshness check. The
target roles are junior data engineering positions in Europe.

## Decision
Use Apache Airflow 3 with one DAG that runs ingestion → dbt build → publish, with
retries and an `on_failure_callback` alert.

## Alternatives considered
- **Dagster**: nicer local developer experience and first-class dbt assets; it
  would be the pick for a greenfield team. It lost because Airflow appears in far
  more European job ads, and showing it is the explicit goal of this portfolio.
- **Cron + scripts**: no retries, no UI, no dependency graph, no alerting.
- **Prefect**: similar trade-off to Dagster, smaller footprint in job ads.

## Consequences
- Airflow is heavy (~4 GB RAM in Docker with Postgres + scheduler + API server).
- Tasks call the project's own CLIs (`python -m ingestion`, `dbt build`), so the
  business logic stays testable without Airflow and portable to another
  orchestrator. Revisit if the DAG grows into many dbt-model-level tasks, where
  Dagster's asset model shines.
