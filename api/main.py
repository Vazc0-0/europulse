"""FastAPI application factory.

Run with ``uvicorn api.main:app``. The API is read-only and public: it exposes the
gold layer of the serving DuckDB copy and nothing else.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import cost_of_living, countries, health, indicators, revisions
from common.warehouse import WarehouseNotReadyError

# Router registration lives in ONE list so adding a resource (for example the
# future AI router ``api/routers/ask.py``) is a one-line change.
ROUTERS = [
    health.router,
    countries.router,
    indicators.router,
    cost_of_living.router,
    revisions.router,
]

DESCRIPTION = (
    "How did inflation, electricity prices and unemployment evolve across EU countries, "
    "and who was hit hardest? Read-only data from Eurostat (gold layer). "
    "Rates are percentages; electricity prices are EUR/kWh."
)


def create_app() -> FastAPI:
    app = FastAPI(title="EuroPulse API", version="0.1.0", description=DESCRIPTION)

    # Public, read-only data: any origin may issue GET requests.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

    @app.exception_handler(WarehouseNotReadyError)
    async def warehouse_not_ready(_request: Request, exc: WarehouseNotReadyError) -> JSONResponse:
        # 503 (not 500): the service is fine, the data simply is not there yet.
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
