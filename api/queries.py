"""All SQL of the API lives here, in one place.

Safety rules (do not break them when adding queries):

* Every value that comes from a request goes through DuckDB parameters (``?``).
* The only things ever formatted into SQL text are table/column names taken from
  the fixed ``INDICATORS`` registry below, and runs of ``?`` placeholders whose
  count (not content) depends on the request. A request can never choose a table.
* Only the gold ``marts`` schema is referenced (docs/contracts.md section 4).

Note on DuckDB 2.0: relations keep no results, so we always use
``con.execute(sql, params).fetchall()`` on a connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb

AGGREGATE_BENCHMARK = "EU27_2020"


@dataclass(frozen=True)
class IndicatorSpec:
    """Static description of one indicator and where its gold data lives."""

    id: str
    label: str
    unit: str
    frequency: str
    dataset: str
    table: str
    value_col: str
    date_col: str  # DATE column used for filtering and ordering
    period_expr: str  # SQL expression producing the display period label


INDICATORS: dict[str, IndicatorSpec] = {
    "inflation": IndicatorSpec(
        id="inflation",
        label="Inflation (HICP, annual rate of change)",
        unit="percent",
        frequency="monthly",
        dataset="prc_hicp_minr",
        table="marts.fct_inflation_monthly",
        value_col="inflation_rate_pct",
        date_col="period_month",
        period_expr="strftime(f.period_month, '%Y-%m')",
    ),
    "unemployment": IndicatorSpec(
        id="unemployment",
        label="Unemployment rate",
        unit="percent",
        frequency="monthly",
        dataset="une_rt_m",
        table="marts.fct_unemployment_monthly",
        value_col="unemployment_rate_pct",
        date_col="period_month",
        period_expr="strftime(f.period_month, '%Y-%m')",
    ),
    "electricity_price": IndicatorSpec(
        id="electricity_price",
        label="Electricity price, household consumers (all taxes included)",
        unit="EUR/kWh",
        frequency="semiannual",
        dataset="nrg_pc_204",
        table="marts.fct_electricity_price_semiannual",
        value_col="price_eur_kwh",
        date_col="period_start",
        period_expr="f.period_semester",
    ),
}


def _placeholders(n: int) -> str:
    return ", ".join("?" * n)


def month_to_date(period: str) -> date:
    """'2026-09' -> date(2026, 9, 1)."""
    year, month = period.split("-")
    return date(int(year), int(month), 1)


def semester_start(period: str) -> date:
    """Map 'YYYY-S1'/'YYYY-S2' or 'YYYY-MM' to the first day of the semester."""
    year, part = period.split("-")
    if part.startswith("S"):
        return date(int(year), 1 if part == "S1" else 7, 1)
    return date(int(year), 1 if int(part) <= 6 else 7, 1)


# --------------------------------------------------------------------- health


def newest_source_update(con: duckdb.DuckDBPyConnection) -> datetime | None:
    row = con.execute(
        """
        SELECT max(ts) FROM (
            SELECT max(source_last_update) AS ts FROM marts.fct_inflation_monthly
            UNION ALL SELECT max(source_last_update) FROM marts.fct_unemployment_monthly
            UNION ALL SELECT max(source_last_update) FROM marts.fct_electricity_price_semiannual
        )
        """
    ).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------ countries


def list_countries(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cur = con.execute(
        """
        SELECT country_code, country_name, is_eu_member, is_euro_area, is_aggregate
        FROM marts.dim_country
        ORDER BY is_aggregate, country_name
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# --------------------------------------------------------------------- series


def get_series(
    con: duckdb.DuckDBPyConnection,
    spec: IndicatorSpec,
    countries: list[str],
    start: date | None,
    end: date | None,
) -> list[dict[str, Any]]:
    """Time series per country. ``countries`` empty means all countries."""
    conditions = [f"f.{spec.value_col} IS NOT NULL"]
    params: list[Any] = []
    if countries:
        conditions.append(f"f.country_code IN ({_placeholders(len(countries))})")
        params.extend(countries)
    if start:
        conditions.append(f"f.{spec.date_col} >= ?")
        params.append(start)
    if end:
        conditions.append(f"f.{spec.date_col} <= ?")
        params.append(end)
    # Table and column names come from the fixed registry, never from the request.
    sql = f"""
        SELECT f.country_code, c.country_name, {spec.period_expr} AS period,
               f.{spec.date_col} AS period_start, f.{spec.value_col} AS value, f.obs_flag
        FROM {spec.table} f
        JOIN marts.dim_country c USING (country_code)
        WHERE {" AND ".join(conditions)}
        ORDER BY f.country_code, f.{spec.date_col}
    """  # noqa: S608
    series: dict[str, dict[str, Any]] = {}
    for code, name, period, period_start, value, flag in con.execute(sql, params).fetchall():
        entry = series.setdefault(code, {"country_code": code, "country_name": name, "points": []})
        entry["points"].append(
            {"period": period, "period_start": period_start, "value": value, "obs_flag": flag}
        )
    return list(series.values())


# ------------------------------------------------------------ cost of living


def _latest_per_country(
    con: duckdb.DuckDBPyConnection, spec: IndicatorSpec, countries: list[str]
) -> dict[str, dict[str, Any]]:
    """Latest non-null value (and its period) for each country."""
    params: list[Any] = []
    where = f"f.{spec.value_col} IS NOT NULL"
    if countries:
        where += f" AND f.country_code IN ({_placeholders(len(countries))})"
        params.extend(countries)
    sql = f"""
        SELECT f.country_code, {spec.period_expr} AS period, f.{spec.value_col} AS value
        FROM {spec.table} f
        WHERE {where}
        QUALIFY row_number() OVER (
            PARTITION BY f.country_code ORDER BY f.{spec.date_col} DESC
        ) = 1
    """  # noqa: S608
    return {
        code: {"period": period, "value": value}
        for code, period, value in con.execute(sql, params).fetchall()
    }


def cost_of_living_latest(
    con: duckdb.DuckDBPyConnection, countries: list[str]
) -> list[dict[str, Any]]:
    inflation = _latest_per_country(con, INDICATORS["inflation"], countries)
    unemployment = _latest_per_country(con, INDICATORS["unemployment"], countries)
    electricity = _latest_per_country(con, INDICATORS["electricity_price"], countries)

    params: list[Any] = list(countries)
    where = f"WHERE country_code IN ({_placeholders(len(countries))})" if countries else ""
    rows = con.execute(
        f"""
        SELECT country_code, country_name, is_aggregate
        FROM marts.dim_country {where}
        ORDER BY is_aggregate, country_name
        """,  # noqa: S608
        params,
    ).fetchall()
    return [
        {
            "country_code": code,
            "country_name": name,
            "is_aggregate": is_agg,
            "inflation": inflation.get(code),
            "unemployment": unemployment.get(code),
            "electricity_price": electricity.get(code),
        }
        for code, name, is_agg in rows
        # Skip geos with no data at all (e.g. a dimension row without facts).
        if code in inflation or code in unemployment or code in electricity
    ]


def latest_period(con: duckdb.DuckDBPyConnection, spec: IndicatorSpec) -> date | None:
    """Newest period start with a value for a real country (aggregates ignored)."""
    row = con.execute(
        f"""
        SELECT max(f.{spec.date_col})
        FROM {spec.table} f
        JOIN marts.dim_country c USING (country_code)
        WHERE NOT c.is_aggregate AND f.{spec.value_col} IS NOT NULL
        """  # noqa: S608
    ).fetchone()
    return row[0] if row else None


def ranking(
    con: duckdb.DuckDBPyConnection, spec: IndicatorSpec, period_start: date, limit: int
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (period label, EU benchmark, ranked countries) for one period.

    Aggregates are excluded from the ranking: comparing "EU27" with its own
    members would be meaningless. The EU27 value is returned separately.
    """
    rows = con.execute(
        f"""
        SELECT f.country_code, c.country_name, c.is_aggregate, {spec.period_expr} AS period,
               f.{spec.value_col} AS value
        FROM {spec.table} f
        JOIN marts.dim_country c USING (country_code)
        WHERE f.{spec.date_col} = ? AND f.{spec.value_col} IS NOT NULL
        ORDER BY value DESC, f.country_code
        """,  # noqa: S608
        [period_start],
    ).fetchall()
    label = rows[0][3] if rows else None
    benchmark = next(
        (
            {"country_code": code, "country_name": name, "value": value}
            for code, name, _agg, _p, value in rows
            if code == AGGREGATE_BENCHMARK
        ),
        None,
    )
    ranked: list[dict[str, Any]] = []
    previous_value: float | None = None
    previous_rank = 0
    for code, name, is_agg, _p, value in rows:
        if is_agg:
            continue
        # Competition ranking: equal values share a rank (1, 2, 2, 4).
        rank = previous_rank if value == previous_value else len(ranked) + 1
        ranked.append({"rank": rank, "country_code": code, "country_name": name, "value": value})
        previous_value, previous_rank = value, rank
    return label, benchmark, ranked[:limit]


# ------------------------------------------------------------------ revisions


def list_revisions(
    con: duckdb.DuckDBPyConnection, dataset: str | None, country: str | None, limit: int
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if dataset:
        conditions.append("dataset_code = ?")
        params.append(dataset)
    if country:
        conditions.append("country_code = ?")
        params.append(country)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    cur = con.execute(
        f"""
        SELECT dataset_code, indicator, country_code, period, previous_value, new_value,
               previous_run_id, revised_run_id, revised_at
        FROM marts.fct_data_revisions
        {where}
        ORDER BY revised_at DESC, dataset_code, country_code, period
        LIMIT ?
        """,  # noqa: S608
        params,
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
