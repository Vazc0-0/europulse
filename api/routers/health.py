"""``GET /health``: liveness plus a hint about data freshness."""

from __future__ import annotations

import duckdb
from fastapi import APIRouter

from api import queries
from api.schemas import HealthResponse
from common.settings import get_settings
from common.warehouse import read_only_connection

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description=(
        "Always answers 200 so a load balancer can probe it. `status` is `degraded` when the "
        "serving database does not exist yet (the pipeline has never run)."
    ),
)
def health() -> HealthResponse:
    if not get_settings().serving_path.exists():
        return HealthResponse(status="degraded", serving_db_exists=False)
    try:
        with read_only_connection() as con:
            newest = queries.newest_source_update(con)
    except duckdb.Error:
        # File exists but is unreadable or lacks the gold tables.
        return HealthResponse(status="degraded", serving_db_exists=True)
    return HealthResponse(status="ok", serving_db_exists=True, latest_source_update=newest)
