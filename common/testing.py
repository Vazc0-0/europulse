"""Build a tiny, deterministic gold-layer database for tests.

The API, dashboard and AI tests must not depend on the network or on a full
pipeline run. This helper creates a DuckDB file with the exact ``marts`` tables
described in docs/contracts.md section 4, filled with a few known values, so
tests can assert exact numbers.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

GOLD_DDL = """
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE marts.dim_country (
    country_code VARCHAR PRIMARY KEY,
    country_name VARCHAR NOT NULL,
    is_eu_member BOOLEAN NOT NULL,
    is_euro_area BOOLEAN NOT NULL,
    is_aggregate BOOLEAN NOT NULL
);

CREATE TABLE marts.fct_inflation_monthly (
    country_code VARCHAR NOT NULL,
    period_month DATE NOT NULL,
    inflation_rate_pct DOUBLE,
    obs_flag VARCHAR,
    source_last_update TIMESTAMP
);

CREATE TABLE marts.fct_unemployment_monthly (
    country_code VARCHAR NOT NULL,
    period_month DATE NOT NULL,
    unemployment_rate_pct DOUBLE,
    obs_flag VARCHAR,
    source_last_update TIMESTAMP
);

CREATE TABLE marts.fct_electricity_price_semiannual (
    country_code VARCHAR NOT NULL,
    period_semester VARCHAR NOT NULL,
    period_start DATE NOT NULL,
    price_eur_kwh DOUBLE,
    obs_flag VARCHAR,
    source_last_update TIMESTAMP
);

CREATE TABLE marts.mart_cost_of_living_monthly (
    country_code VARCHAR NOT NULL,
    country_name VARCHAR NOT NULL,
    period_month DATE NOT NULL,
    inflation_rate_pct DOUBLE,
    unemployment_rate_pct DOUBLE,
    electricity_price_eur_kwh DOUBLE,
    electricity_period_semester VARCHAR
);

CREATE TABLE marts.fct_data_revisions (
    dataset_code VARCHAR NOT NULL,
    indicator VARCHAR NOT NULL,
    country_code VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    previous_value DOUBLE,
    new_value DOUBLE,
    previous_run_id VARCHAR NOT NULL,
    revised_run_id VARCHAR NOT NULL,
    revised_at TIMESTAMP NOT NULL
);
"""

GOLD_SEED = """
INSERT INTO marts.dim_country VALUES
    ('DE', 'Germany', true, true, false),
    ('PT', 'Portugal', true, true, false),
    ('PL', 'Poland', true, false, false),
    ('EU27_2020', 'European Union (27)', false, false, true);

-- Two months x four geos of inflation and unemployment.
INSERT INTO marts.fct_inflation_monthly VALUES
    ('DE', DATE '2026-08-01', 2.1, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('DE', DATE '2026-09-01', 2.3, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('PT', DATE '2026-08-01', 3.6, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('PT', DATE '2026-09-01', 3.6, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('PL', DATE '2026-08-01', 4.4, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('PL', DATE '2026-09-01', 4.0, 'p', TIMESTAMP '2026-10-16 23:00:00'),
    ('EU27_2020', DATE '2026-08-01', 2.5, NULL, TIMESTAMP '2026-10-16 23:00:00'),
    ('EU27_2020', DATE '2026-09-01', 2.6, NULL, TIMESTAMP '2026-10-16 23:00:00');

INSERT INTO marts.fct_unemployment_monthly VALUES
    ('DE', DATE '2026-08-01', 3.9, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('DE', DATE '2026-09-01', 4.0, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('PT', DATE '2026-08-01', 6.1, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('PT', DATE '2026-09-01', 6.0, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('PL', DATE '2026-08-01', 2.8, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('PL', DATE '2026-09-01', 2.9, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('EU27_2020', DATE '2026-08-01', 5.9, NULL, TIMESTAMP '2026-10-21 11:00:00'),
    ('EU27_2020', DATE '2026-09-01', 5.9, NULL, TIMESTAMP '2026-10-21 11:00:00');

INSERT INTO marts.fct_electricity_price_semiannual VALUES
    ('DE', '2026-S1', DATE '2026-01-01', 0.3951, NULL, TIMESTAMP '2026-10-19 23:00:00'),
    ('PT', '2026-S1', DATE '2026-01-01', 0.2410, NULL, TIMESTAMP '2026-10-19 23:00:00'),
    ('PL', '2026-S1', DATE '2026-01-01', 0.2600, 'e', TIMESTAMP '2026-10-19 23:00:00'),
    ('EU27_2020', '2026-S1', DATE '2026-01-01', 0.2880, NULL, TIMESTAMP '2026-10-19 23:00:00');

INSERT INTO marts.mart_cost_of_living_monthly
SELECT i.country_code, c.country_name, i.period_month, i.inflation_rate_pct,
       u.unemployment_rate_pct, NULL, NULL
FROM marts.fct_inflation_monthly i
JOIN marts.dim_country c USING (country_code)
LEFT JOIN marts.fct_unemployment_monthly u USING (country_code, period_month);

INSERT INTO marts.fct_data_revisions VALUES
    ('prc_hicp_minr', 'inflation_rate_pct', 'PL', '2026-08', 4.2, 4.4,
     '20261015T231500Z', '20261016T231500Z', TIMESTAMP '2026-10-16 23:15:00');
"""


def build_gold_db(path: Path) -> Path:
    """Create (or replace) a small gold database at ``path`` and return the path."""
    path.unlink(missing_ok=True)
    with duckdb.connect(str(path)) as con:
        con.execute(GOLD_DDL)
        con.execute(GOLD_SEED)
    return path
