# Data contracts between layers

This file is the single source of truth for what each layer produces and consumes.
If code and this file disagree, the code is wrong (or this file must be updated in
the same commit).

```
Eurostat SDMX API ──► ingestion ──► BRONZE (Parquet, immutable)
                                       │  dbt sources
                                       ▼
                               SILVER  staging.*      (views, typed, all snapshots)
                                       intermediate.* (latest values, history)
                                       ▼
                               GOLD    marts.*        (tables) ──► publish ──► serving copy
                                                                               │
                                              API · dashboard · AI read ONLY ◄─┘
```

## 1. Paths

All paths hang off `EUROPULSE_DATA_DIR` (default `./data`, `/opt/europulse/data` in Docker).

| What | Path |
|---|---|
| Bronze file | `bronze/eurostat/dataset=<code>/ingestion_date=<YYYY-MM-DD>/<run_id>.parquet` |
| Run manifest | `bronze/eurostat/_runs/<run_id>.json` |
| Pipeline warehouse (dbt writes) | `warehouse/europulse.duckdb` |
| Serving copy (consumers read) | `warehouse/europulse_serving.duckdb` |

`run_id` = UTC timestamp of the ingestion run, format `YYYYMMDDTHHMMSSZ`
(e.g. `20261028T231500Z`). One run writes one file per dataset; all files of a run
share the same `run_id`.

## 2. Bronze (ingestion output)

Immutable. A file is **never** overwritten or deleted by the pipeline: running
ingestion twice produces two files. This is what preserves the revision history
Eurostat itself does not keep.

Columns of every bronze Parquet file:

| Column | Type | Meaning |
|---|---|---|
| `dataflow` | VARCHAR | Raw SDMX `DATAFLOW`, e.g. `ESTAT:PRC_HICP_MINR(1.0)` |
| `last_update` | VARCHAR | Raw SDMX `LAST UPDATE`, format `dd/mm/yy HH:MM:SS` (Eurostat local time) |
| `freq` … dataset dims | VARCHAR | Every SDMX dimension column, lower-cased, as delivered |
| `geo` | VARCHAR | Eurostat country code (`EL` = Greece, `EU27_2020` = EU aggregate) |
| `time_period` | VARCHAR | `YYYY-MM` (monthly) or `YYYY-S1`/`YYYY-S2` (semiannual) |
| `obs_value` | VARCHAR | Raw value as text; may be empty |
| `obs_flag` | VARCHAR | Eurostat flag (`e` estimated, `p` provisional, `b` break …) or NULL |
| `conf_status` | VARCHAR | Confidentiality status or NULL |
| `_dataset_code` | VARCHAR | Lower-case dataset code, e.g. `prc_hicp_minr` |
| `_run_id` | VARCHAR | See above |
| `_ingested_at` | TIMESTAMP (UTC) | When the response was received |
| `_source_url` | VARCHAR | Exact URL requested |

Column names are the SDMX-CSV header lower-cased with spaces replaced by `_`.
Values stay as text in bronze: typing is the job of silver.

Run manifest (`_runs/<run_id>.json`):
`{run_id, started_at, finished_at, status: "success"|"partial"|"failed", datasets: {<code>: {status, rows, file, source_last_update, error}}}`.

## 3. Silver (dbt `staging` and `intermediate` schemas)

| Model | Grain | Key columns |
|---|---|---|
| `staging.stg_eurostat__hicp` | one row per country × month × run | `country_code, period_month (DATE, 1st of month), inflation_rate_pct, obs_flag, source_last_update, run_id, ingested_at` |
| `staging.stg_eurostat__unemployment` | country × month × run | `country_code, period_month, unemployment_rate_pct, obs_flag, source_last_update, run_id, ingested_at` |
| `staging.stg_eurostat__electricity_prices` | country × semester × run | `country_code, period_semester ('2024-S1'), period_start (DATE: S1→Jan 1, S2→Jul 1), price_eur_kwh, obs_flag, source_last_update, run_id, ingested_at` |
| `intermediate.int_observations_history` | dataset × country × period × run | long format of the three above: `dataset_code, indicator, country_code, period (VARCHAR), period_start (DATE), value, obs_flag, run_id, ingested_at` |
| `intermediate.int_observations_latest` | dataset × country × period | the row from the most recent run (`ingested_at` max) |

## 4. Gold (dbt `marts` schema) — the public contract

Only these tables are readable by the API, the dashboard and the AI layer.

| Table | Grain | Columns |
|---|---|---|
| `marts.dim_country` | one row per geo | `country_code, country_name, is_eu_member, is_euro_area, is_aggregate` |
| `marts.fct_inflation_monthly` | country × month | `country_code, period_month, inflation_rate_pct, obs_flag, source_last_update` |
| `marts.fct_unemployment_monthly` | country × month | `country_code, period_month, unemployment_rate_pct, obs_flag, source_last_update` |
| `marts.fct_electricity_price_semiannual` | country × semester | `country_code, period_semester, period_start, price_eur_kwh, obs_flag, source_last_update` |
| `marts.mart_cost_of_living_monthly` | country × month | `country_code, country_name, period_month, inflation_rate_pct, unemployment_rate_pct, electricity_price_eur_kwh` (price of the semester containing the month), `electricity_period_semester` |
| `marts.fct_data_revisions` | one row per changed value | `dataset_code, indicator, country_code, period, previous_value, new_value, previous_run_id, revised_run_id, revised_at` |

Snapshot `snapshots.snap_observations` (dbt snapshot, check strategy on `value`,
unique key `dataset_code || '|' || country_code || '|' || period`) tracks revisions
with `dbt_valid_from/dbt_valid_to`; `fct_data_revisions` is derived from bronze
history directly, so it works even on a fresh clone that only has bronze files.

Units: rates are in percent (`3.6` means 3.6 %); electricity price in EUR per kWh.

## 5. Consumers

* Open the serving copy with `common.warehouse.read_only_connection()`.
* Never read bronze, `staging` or `intermediate`.
* The AI layer may only reference the tables listed in section 4.
