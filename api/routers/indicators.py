"""``/indicators`` (metadata) and ``/indicators/{id}/series`` (time series)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api import queries
from api.routers._params import MONTH_PATTERN, parse_countries
from api.schemas import Indicator, SeriesResponse
from common.warehouse import read_only_connection

router = APIRouter(tags=["indicators"])


@router.get(
    "/indicators",
    response_model=list[Indicator],
    summary="Describe the available indicators",
    description="Static metadata: unit, frequency, Eurostat dataset code and gold table.",
)
def indicators() -> list[Indicator]:
    return [
        Indicator(
            id=s.id,  # type: ignore[arg-type]
            label=s.label,
            unit=s.unit,
            frequency=s.frequency,  # type: ignore[arg-type]
            source_dataset=s.dataset,
            gold_table=s.table,
        )
        for s in queries.INDICATORS.values()
    ]


@router.get(
    "/indicators/{indicator_id}/series",
    response_model=SeriesResponse,
    summary="Time series of one indicator",
    description=(
        "One series per country. `countries` is a comma-separated list (default: all). "
        "`start`/`end` are `YYYY-MM` and inclusive; for the semiannual electricity price they "
        "filter on the first day of the semester."
    ),
    responses={404: {"description": "Unknown indicator id."}},
)
def series(
    indicator_id: str,
    countries: Annotated[
        str | None, Query(description="Comma-separated codes, e.g. `PT,DE`.", examples=["PT,DE"])
    ] = None,
    start: Annotated[
        str | None, Query(pattern=MONTH_PATTERN, description="First month, `YYYY-MM`.")
    ] = None,
    end: Annotated[
        str | None, Query(pattern=MONTH_PATTERN, description="Last month, `YYYY-MM`.")
    ] = None,
) -> SeriesResponse:
    # Look the indicator up in the fixed registry: the request never names a table.
    spec = queries.INDICATORS.get(indicator_id)
    if spec is None:
        raise HTTPException(
            404, f"Unknown indicator '{indicator_id}'. Valid: {', '.join(queries.INDICATORS)}."
        )
    codes = parse_countries(countries)
    start_date = queries.month_to_date(start) if start else None
    end_date = queries.month_to_date(end) if end else None
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "`start` must not be after `end`.")
    with read_only_connection() as con:
        data = queries.get_series(con, spec, codes, start_date, end_date)
    return SeriesResponse(
        indicator=spec.id,  # type: ignore[arg-type]
        unit=spec.unit,
        frequency=spec.frequency,
        series=data,
    )
