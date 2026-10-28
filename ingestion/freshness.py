"""Freshness check: is the newest successful run recent enough?

Meant for a scheduler or CI to alert when the daily ingestion silently stopped working.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ingestion.schemas import RunManifest

logger = logging.getLogger("ingestion.freshness")


@dataclass(frozen=True)
class FreshnessReport:
    ok: bool
    message: str
    run_id: str | None = None
    age_hours: float | None = None


def latest_successful_manifest(bronze_dir: Path) -> RunManifest | None:
    best: RunManifest | None = None
    for path in sorted((bronze_dir / "_runs").glob("*.json")):
        try:
            manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            # One corrupt manifest must not hide the healthy ones.
            logger.warning(
                "ignoring unreadable manifest", extra={"file": str(path), "error": str(exc)}
            )
            continue
        # Only fully successful runs count: a partial run is missing at least one dataset.
        if manifest.status == "success" and (
            best is None or manifest.finished_at > best.finished_at
        ):
            best = manifest
    return best


def check_freshness(
    bronze_dir: Path, max_age_hours: float, now: datetime | None = None
) -> FreshnessReport:
    now = now or datetime.now(UTC)
    manifest = latest_successful_manifest(bronze_dir)
    if manifest is None:
        return FreshnessReport(
            False, f"STALE: no successful run manifest found in {bronze_dir}/_runs"
        )
    age = (now - manifest.finished_at).total_seconds() / 3600
    summary = (
        f"newest successful run {manifest.run_id} finished {manifest.finished_at.isoformat()} "
        f"({age:.1f} h ago, limit {max_age_hours:g} h)"
    )
    if age > max_age_hours:
        return FreshnessReport(False, f"STALE: {summary}", manifest.run_id, age)
    return FreshnessReport(True, f"FRESH: {summary}", manifest.run_id, age)
