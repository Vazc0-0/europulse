"""``GET /revisions``: values Eurostat changed after first publishing them."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from api import queries
from api.routers._params import COUNTRY_RE
from api.schemas import Revision
from common.warehouse import read_only_connection

router = APIRouter(tags=["revisions"])


@router.get(
    "/revisions",
    response_model=list[Revision],
    summary="Data revisions, newest first",
    description=(
        "Eurostat overwrites its data in place and keeps no history. This pipeline keeps every "
        "ingested snapshot, so it can show what changed and when."
    ),
)
def revisions(
    dataset: Annotated[
        str | None,
        Query(pattern=r"^[a-z0-9_]{2,40}$", description="Dataset code, e.g. `prc_hicp_minr`."),
    ] = None,
    country: Annotated[
        str | None,
        Query(pattern=COUNTRY_RE.pattern, description="Country code, e.g. `PL`."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Revision]:
    with read_only_connection() as con:
        return [Revision(**row) for row in queries.list_revisions(con, dataset, country, limit)]
