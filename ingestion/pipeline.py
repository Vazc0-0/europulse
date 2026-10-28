"""Orchestrates a run: download -> validate -> write bronze -> write manifest.

Kept separate from the CLI so tests (and later an orchestrator such as Airflow) can call
``run_ingestion`` directly with an injected clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from common.settings import Settings
from ingestion import bronze
from ingestion.eurostat_client import EurostatClient
from ingestion.schemas import (
    DatasetConfig,
    DatasetRunResult,
    DatasetsFile,
    RunManifest,
    overall_status,
)

logger = logging.getLogger("ingestion.pipeline")

FIXTURES_DIR = Path(__file__).resolve().parent / "tests" / "fixtures"

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _record(
    csv_text: str,
    dataset: DatasetConfig,
    *,
    settings: Settings,
    run_id: str,
    ingestion_date: str,
    ingested_at: datetime,
    source_url: str,
) -> DatasetRunResult:
    """Validate + write one dataset, returning its manifest entry."""
    parsed = bronze.parse_and_validate(csv_text, dataset.code)
    path = bronze.write_bronze_file(
        parsed,
        bronze_dir=settings.bronze_dir,
        dataset_code=dataset.code,
        run_id=run_id,
        ingestion_date=ingestion_date,
        ingested_at=ingested_at,
        source_url=source_url,
    )
    logger.info(
        "bronze written", extra={"dataset": dataset.code, "rows": parsed.rows, "file": str(path)}
    )
    return DatasetRunResult(
        status="success",
        rows=parsed.rows,
        file=path.relative_to(settings.data_dir).as_posix(),
        source_last_update=parsed.source_last_update,
    )


def _run(
    config: DatasetsFile,
    datasets: list[DatasetConfig],
    settings: Settings,
    clock: Clock,
    get_csv: Callable[[DatasetConfig], tuple[str, str, datetime]],
) -> RunManifest:
    started_at = clock()
    run_id = bronze.make_run_id(started_at)
    ingestion_date = started_at.astimezone(UTC).date().isoformat()
    results: dict[str, DatasetRunResult] = {}

    for dataset in datasets:
        # A failure in one dataset must never stop the others, so catch broadly here and
        # record the reason in the manifest.
        try:
            text, url, fetched_at = get_csv(dataset)
            results[dataset.code] = _record(
                text,
                dataset,
                settings=settings,
                run_id=run_id,
                ingestion_date=ingestion_date,
                ingested_at=fetched_at,
                source_url=url,
            )
        except Exception as exc:
            logger.error("dataset failed", extra={"dataset": dataset.code, "error": str(exc)})
            results[dataset.code] = DatasetRunResult(status="failed", error=str(exc))

    manifest = RunManifest(
        run_id=run_id,
        started_at=started_at,
        finished_at=clock(),
        status=overall_status(results),
        datasets=results,
    )
    path = bronze.write_manifest(manifest, settings.bronze_dir)
    logger.info(
        "run finished", extra={"run_id": run_id, "status": manifest.status, "file": str(path)}
    )
    return manifest


def run_ingestion(
    config: DatasetsFile,
    settings: Settings,
    *,
    only: list[str] | None = None,
    clock: Clock = _utc_now,
    backoff_initial: float = 1.0,
) -> RunManifest:
    """Download the configured datasets from Eurostat (or just ``only``)."""
    datasets = [d for d in config.datasets if not only or d.code in only]
    with EurostatClient(
        config.base_url,
        timeout=settings.http_timeout_seconds,
        max_attempts=settings.http_max_attempts,
        backoff_initial=backoff_initial,
    ) as client:

        def get_csv(dataset: DatasetConfig) -> tuple[str, str, datetime]:
            result = client.fetch_dataset(dataset, config.geos)
            return result.text, result.url, result.fetched_at

        return _run(config, datasets, settings, clock, get_csv)


def load_fixtures(
    config: DatasetsFile,
    settings: Settings,
    *,
    fixtures_dir: Path = FIXTURES_DIR,
    clock: Clock = _utc_now,
) -> RunManifest:
    """Write bronze files from the saved CSVs as if they were a real run.

    Lets CI run dbt without network access. The files go through exactly the same
    validation and writing code as real downloads.
    """

    def get_csv(dataset: DatasetConfig) -> tuple[str, str, datetime]:
        path = fixtures_dir / f"{dataset.code}.csv"
        return path.read_text(encoding="utf-8-sig"), path.as_uri(), clock()

    return _run(config, config.datasets, settings, clock, get_csv)
