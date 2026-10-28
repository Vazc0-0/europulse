"""``/cost-of-living/latest`` and ``/cost-of-living/ranking``."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.routers._params import parse_countries
from api.schemas import CostOfLivingLatest, IndicatorId, RankingResponse
from common.warehouse import read_only_connection

router = APIRouter(prefix="/cost-of-living", tags=["cost of living"])

# Months ('2026-09') for every indicator; semesters ('2026-S1') only for electricity.
PERIOD_RE = re.compile(r"^\d{4}-((0[1-9]|1[0-2])|S[12])$")


@router.get(
    "/latest",
    response_model=list[CostOfLivingLatest],
    summary="Latest indicators per country",
    description=(
        "For each country: the latest month with inflation and unemployment and the latest "
        "electricity price, each with its own period (the sources publish at different times)."
    ),
)
def latest(
    countries: Annotated[
        str | None, Query(description="Comma-separated codes; default all.", examples=["PT,DE"])
    ] = None,
) -> list[CostOfLivingLatest]:
    codes = parse_countries(countries)
    with read_only_connection() as con:
        return [CostOfLivingLatest(**row) for row in queries.cost_of_living_latest(con, codes)]


@router.get(
    "/ranking",
    response_model=RankingResponse,
    summary="Rank countries by one indicator",
    description=(
        "Countries sorted from highest to lowest value for one period (default: the latest "
        "available). Aggregates are excluded from the ranking; the EU27 value comes back "
        "separately as `benchmark`. `period` is `YYYY-MM`; for electricity a month maps to the "
        "semester containing it and `YYYY-S1`/`YYYY-S2` is also accepted."
    ),
)
def ranking(
    indicator: Annotated[IndicatorId, Query(description="Indicator to rank.")] = "inflation",
    period: Annotated[str | None, Query(description="`YYYY-MM` (default: latest).")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> RankingResponse:
    spec = queries.INDICATORS[indicator]
    if period is not None:
        if not PERIOD_RE.fullmatch(period):
            raise HTTPException(
                422, "`period` must look like 2026-09 (or 2026-S1 for electricity)."
            )
        if "S" in period and spec.frequency != "semiannual":
            raise HTTPException(422, "Semester periods are only valid for electricity_price.")
    with read_only_connection() as con:
        if period is None:
            period_start = queries.latest_period(con, spec)
        elif spec.frequency == "semiannual":
            period_start = queries.semester_start(period)
        else:
            period_start = queries.month_to_date(period)
        if period_start is None:  # no data at all for this indicator
            label, benchmark, rows = None, None, []
        else:
            label, benchmark, rows = queries.ranking(con, spec, period_start, limit)
    return RankingResponse(
        indicator=indicator,
        unit=spec.unit,
        period=label,
        benchmark=benchmark,
        ranking=rows,
    )
