"""``GET /countries``: the country dimension."""

from __future__ import annotations

from fastapi import APIRouter

from api import queries
from api.schemas import Country
from common.warehouse import read_only_connection

router = APIRouter(tags=["reference"])


@router.get(
    "/countries",
    response_model=list[Country],
    summary="List countries and aggregates",
    description=(
        "Rows of `marts.dim_country`. Aggregates such as `EU27_2020` have `is_aggregate=true`."
    ),
)
def countries() -> list[Country]:
    with read_only_connection() as con:
        return [Country(**row) for row in queries.list_countries(con)]
