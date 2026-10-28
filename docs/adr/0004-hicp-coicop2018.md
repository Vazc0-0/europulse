# 0004 — Use prc_hicp_minr (COICOP 2018) instead of prc_hicp_manr

- **Status:** accepted
- **Date:** 2026-10-28

## Context
The project plan named `prc_hicp_manr` for monthly inflation. Checking the live
API on 2026-10-28 showed that dataset frozen: last update 06/02/2026, last period
2025-12. In 2026 Eurostat moved the HICP to the COICOP 2018 classification and
publishes current data in `prc_hicp_minr` (dimension `coicop18`, all-items code
`TOTAL`, annual rate unit `RCH_A`), with history back to at least 2019.

The same check showed that the SDMX 2.1 endpoint ignores query-string filters
such as `geo=PT`: the request silently returned every country. Filters only work
inside the series key (`M.RCH_A.TOTAL.PT+DE`).

## Decision
Ingest `prc_hicp_minr` with key `M.RCH_A.TOTAL.{geos}` and put every filter in the
SDMX key (see `config/datasets.yaml`).

## Alternatives considered
- Keep `prc_hicp_manr`: the dashboard would silently stop at December 2025.
- Stitch both series: unnecessary while `minr` carries the full history.

## Consequences
- A real example of upstream structure change, the reason the ingestion layer
  validates the CSV header against the expected dimensions and fails loudly.
- If Eurostat retires a dataset again, the header check fails the run and the
  freshness check alerts, instead of the pipeline serving stale numbers.
