"""Shared test helpers: a tiny config, fixture CSV access and a controllable clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from common.settings import Settings
from ingestion.schemas import DatasetsFile

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://example.test/sdmx/2.1/data"


def fixture_csv(code: str) -> str:
    return (FIXTURES / f"{code}.csv").read_text(encoding="utf-8")


def make_clock(start: datetime):
    """Clock that advances one second per call, so run_id/finished_at are deterministic."""
    state = {"now": start}

    def clock() -> datetime:
        current = state["now"]
        state["now"] = current + timedelta(seconds=1)
        return current

    return clock


@pytest.fixture
def config() -> DatasetsFile:
    return DatasetsFile.model_validate(
        {
            "base_url": BASE_URL,
            "geos": ["PT", "EL"],
            "datasets": {
                "prc_hicp_minr": {
                    "key": "M.RCH_A.TOTAL.{geo}",
                    "start_period": "2019-01",
                    "frequency": "monthly",
                },
                "une_rt_m": {
                    "key": "M.SA.TOTAL.PC_ACT.T.{geo}",
                    "start_period": "2019-01",
                    "frequency": "monthly",
                },
                "nrg_pc_204": {
                    "key": "S.E7000.KWH2500-4999.KWH.I_TAX.EUR.{geo}",
                    "start_period": "2019-S1",
                    "frequency": "semiannual",
                },
            },
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", http_max_attempts=3, http_timeout_seconds=5)


@pytest.fixture
def clock():
    return make_clock(datetime(2026, 10, 28, 23, 15, 0, tzinfo=UTC))
