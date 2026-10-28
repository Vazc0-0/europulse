"""Pydantic response models of the API.

Why typed response models? FastAPI uses them to (1) validate what we return,
(2) generate the OpenAPI document (and the Swagger UI at ``/docs``) and
(3) strip anything we did not declare. The ``examples`` use values from the real
gold layer so the documentation shows realistic payloads.

Units everywhere: rates are percent (3.6 means 3.6 %), electricity is EUR/kWh.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IndicatorId = Literal["inflation", "unemployment", "electricity_price"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="'ok' when the serving database exists and can be read."
    )
    serving_db_exists: bool = Field(description="False until the pipeline has run once.")
    latest_source_update: datetime | None = Field(
        default=None,
        description="Newest Eurostat 'last update' timestamp found across the fact tables.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "serving_db_exists": True,
                "latest_source_update": "2026-10-21T11:00:00",
            }
        }
    )


class Country(BaseModel):
    country_code: str = Field(description="Eurostat geo code (EL = Greece).")
    country_name: str
    is_eu_member: bool
    is_euro_area: bool
    is_aggregate: bool = Field(description="True for aggregates such as EU27_2020.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country_code": "PT",
                "country_name": "Portugal",
                "is_eu_member": True,
                "is_euro_area": True,
                "is_aggregate": False,
            }
        }
    )


class Indicator(BaseModel):
    id: IndicatorId
    label: str
    unit: str
    frequency: Literal["monthly", "semiannual"]
    source_dataset: str = Field(description="Eurostat dataset code.")
    gold_table: str = Field(description="Gold table the values are read from.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "inflation",
                "label": "Inflation (HICP, annual rate of change)",
                "unit": "percent",
                "frequency": "monthly",
                "source_dataset": "prc_hicp_minr",
                "gold_table": "marts.fct_inflation_monthly",
            }
        }
    )


class SeriesPoint(BaseModel):
    period: str = Field(
        description="'YYYY-MM' for monthly data, 'YYYY-S1'/'YYYY-S2' for semesters."
    )
    period_start: date = Field(description="First day of the period.")
    value: float | None
    obs_flag: str | None = Field(default=None, description="Eurostat flag, e.g. 'p' provisional.")


class CountrySeries(BaseModel):
    country_code: str
    country_name: str
    points: list[SeriesPoint]


class SeriesResponse(BaseModel):
    indicator: IndicatorId
    unit: str
    frequency: str
    series: list[CountrySeries] = Field(
        description="One entry per requested country that has data; unknown codes are omitted."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "indicator": "inflation",
                "unit": "percent",
                "frequency": "monthly",
                "series": [
                    {
                        "country_code": "PT",
                        "country_name": "Portugal",
                        "points": [
                            {
                                "period": "2026-08",
                                "period_start": "2026-08-01",
                                "value": 3.6,
                                "obs_flag": None,
                            }
                        ],
                    }
                ],
            }
        }
    )


class LatestValue(BaseModel):
    period: str = Field(description="Period the value belongs to ('2026-09' or '2026-S1').")
    value: float


class CostOfLivingLatest(BaseModel):
    country_code: str
    country_name: str
    is_aggregate: bool
    inflation: LatestValue | None = Field(description="Latest month with an inflation value.")
    unemployment: LatestValue | None = Field(description="Latest month with an unemployment value.")
    electricity_price: LatestValue | None = Field(
        description="Latest semester with an electricity price (EUR/kWh)."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country_code": "PT",
                "country_name": "Portugal",
                "is_aggregate": False,
                "inflation": {"period": "2026-09", "value": 3.6},
                "unemployment": {"period": "2026-09", "value": 6.0},
                "electricity_price": {"period": "2026-S1", "value": 0.241},
            }
        }
    )


class RankingEntry(BaseModel):
    rank: int = Field(description="1 = highest value (ties share a rank).")
    country_code: str
    country_name: str
    value: float


class Benchmark(BaseModel):
    country_code: str
    country_name: str
    value: float


class RankingResponse(BaseModel):
    indicator: IndicatorId
    unit: str
    period: str | None = Field(description="Period ranked; null when the indicator has no data.")
    order: Literal["desc"] = "desc"
    benchmark: Benchmark | None = Field(
        description="EU27_2020 value for the same period, kept out of the ranking."
    )
    ranking: list[RankingEntry]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "indicator": "inflation",
                "unit": "percent",
                "period": "2026-09",
                "order": "desc",
                "benchmark": {
                    "country_code": "EU27_2020",
                    "country_name": "European Union (27)",
                    "value": 2.6,
                },
                "ranking": [
                    {"rank": 1, "country_code": "PL", "country_name": "Poland", "value": 4.0}
                ],
            }
        }
    )


class Revision(BaseModel):
    dataset_code: str
    indicator: str
    country_code: str
    period: str
    previous_value: float | None
    new_value: float | None
    previous_run_id: str
    revised_run_id: str
    revised_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "dataset_code": "prc_hicp_minr",
                "indicator": "inflation_rate_pct",
                "country_code": "PL",
                "period": "2026-08",
                "previous_value": 4.2,
                "new_value": 4.4,
                "previous_run_id": "20261015T231500Z",
                "revised_run_id": "20261016T231500Z",
                "revised_at": "2026-10-16T23:15:00",
            }
        }
    )
