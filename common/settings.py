"""Central configuration for every EuroPulse layer.

All paths derive from a single data directory (``EUROPULSE_DATA_DIR``) so the
same code runs on a laptop, inside Docker (``/opt/europulse/data``) and in CI.
Values come from environment variables or a local ``.env`` file, never from code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EUROPULSE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Root folder for all data. Layout (see docs/contracts.md):
    #   bronze/eurostat/dataset=<code>/ingestion_date=<YYYY-MM-DD>/<run_id>.parquet
    #   warehouse/europulse.duckdb          <- written by dbt (pipeline only)
    #   warehouse/europulse_serving.duckdb  <- read-only copy for API/dashboard/AI
    data_dir: Path = Field(default=REPO_ROOT / "data")
    datasets_config: Path = Field(default=REPO_ROOT / "config" / "datasets.yaml")

    # HTTP behaviour of the Eurostat client.
    http_timeout_seconds: float = 120.0
    http_max_attempts: int = 5

    @property
    def bronze_dir(self) -> Path:
        return self.data_dir / "bronze" / "eurostat"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_dir / "warehouse"

    @property
    def warehouse_path(self) -> Path:
        return self.warehouse_dir / "europulse.duckdb"

    @property
    def serving_path(self) -> Path:
        return self.warehouse_dir / "europulse_serving.duckdb"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (call ``get_settings.cache_clear()`` in tests)."""
    return Settings()
