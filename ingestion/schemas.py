"""Pydantic models used by the ingestion layer.

Three families live here, all in one place so the "shape of the data" is easy to find:

* configuration models (what ``config/datasets.yaml`` must look like),
* row/header validation (what a Eurostat SDMX-CSV response must look like),
* run manifest models (what we write to ``_runs/<run_id>.json``).

Validating at the boundary means a change on Eurostat's side fails loudly here instead of
silently producing bad bronze files that dbt would trip over days later.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class DatasetConfig(BaseModel):
    """One entry of ``datasets:`` in ``config/datasets.yaml``."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str = ""
    # SDMX series key with a ``{geo}`` placeholder. Filters must live in the key because
    # the SDMX 2.1 endpoint silently ignores unknown query parameters.
    key: str
    start_period: str = Field(pattern=r"^\d{4}(-(\d{2}|S[12]))?$")
    frequency: Literal["monthly", "semiannual"]

    @field_validator("code")
    @classmethod
    def _lower_code(cls, value: str) -> str:
        # Bronze folders and the contract use lower-case codes.
        return value.strip().lower()

    @field_validator("key")
    @classmethod
    def _key_has_geo(cls, value: str) -> str:
        if "{geo}" not in value:
            raise ValueError("key must contain the '{geo}' placeholder")
        return value


class DatasetsFile(BaseModel):
    """The whole ``config/datasets.yaml`` file."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    geos: list[str] = Field(min_length=1)
    datasets: list[DatasetConfig] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _inject_codes(cls, data: Any) -> Any:
        # In YAML the dataset code is the mapping key; we move it inside the model so the
        # rest of the code can pass a single DatasetConfig around.
        if isinstance(data, dict) and isinstance(data.get("datasets"), dict):
            data = {
                **data,
                "datasets": [{"code": code, **spec} for code, spec in data["datasets"].items()],
            }
        return data

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


# ---------------------------------------------------------------------------
# SDMX-CSV validation
# ---------------------------------------------------------------------------

# Columns every response must carry; others (the dataset dimensions) vary per dataset.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "DATAFLOW",
    "LAST UPDATE",
    "freq",
    "geo",
    "TIME_PERIOD",
    "OBS_VALUE",
    "OBS_FLAG",
    "CONF_STATUS",
)


class DataValidationError(ValueError):
    """The response does not look like what we expect from Eurostat."""


def bronze_column_name(csv_header: str) -> str:
    """Contract rule: header lower-cased, spaces replaced by underscores."""
    return csv_header.strip().lower().replace(" ", "_")


def check_header(header: list[str], dataset_code: str) -> None:
    """Fail loudly when Eurostat changed the structure of a dataset."""
    missing = [col for col in EXPECTED_COLUMNS if col not in header]
    if missing:
        raise DataValidationError(
            f"{dataset_code}: CSV header is missing expected column(s) {missing}. "
            f"Eurostat probably changed the dataset structure. Got: {header}"
        )


class RowCheck(BaseModel):
    """Checks the three fields of a CSV row that downstream code depends on.

    Other columns are carried through untouched (``extra='ignore'``) because typing them is
    the job of silver, not bronze.
    """

    model_config = ConfigDict(extra="ignore")

    geo: str = Field(min_length=1)
    time_period: str = Field(pattern=r"^\d{4}-(\d{2}|S[12])$")
    obs_value: str

    @field_validator("obs_value")
    @classmethod
    def _empty_or_float(cls, value: str) -> str:
        # Empty is legitimate (Eurostat uses it for missing observations).
        if value == "":
            return value
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"obs_value {value!r} is not a number") from None
        if not math.isfinite(number):
            raise ValueError(f"obs_value {value!r} is not finite")
        return value


# ---------------------------------------------------------------------------
# Run manifest (docs/contracts.md section 2)
# ---------------------------------------------------------------------------


class DatasetRunResult(BaseModel):
    status: Literal["success", "failed"]
    rows: int | None = None
    # Path of the bronze file relative to the data directory (None when the dataset failed).
    file: str | None = None
    # Raw LAST UPDATE value (dd/mm/yy HH:MM:SS) of the most recently updated row.
    source_last_update: str | None = None
    error: str | None = None


class RunManifest(BaseModel):
    run_id: str = Field(pattern=r"^\d{8}T\d{6}Z$")
    started_at: datetime
    finished_at: datetime
    status: Literal["success", "partial", "failed"]
    datasets: dict[str, DatasetRunResult]


def overall_status(results: dict[str, DatasetRunResult]) -> Literal["success", "partial", "failed"]:
    """success = all ok, failed = all failed, partial = anything in between."""
    ok = sum(1 for r in results.values() if r.status == "success")
    if ok == len(results):
        return "success"
    return "failed" if ok == 0 else "partial"
