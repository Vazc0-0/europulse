"""Writes bronze Parquet files and run manifests (docs/contracts.md sections 1-2).

Bronze is immutable: this module refuses to overwrite an existing file. That rule is what
preserves Eurostat's revision history (see docs/adr/0005-immutable-bronze-snapshots.md).
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from ingestion.schemas import (
    DataValidationError,
    RowCheck,
    RunManifest,
    bronze_column_name,
    check_header,
)

# These two are NULL in bronze when empty; everything else keeps the raw text
# (obs_value may legitimately be an empty string).
_NULLABLE_WHEN_EMPTY = {"obs_flag", "conf_status"}
_MAX_ERRORS_REPORTED = 5


class BronzeExistsError(FileExistsError):
    """Refusing to overwrite an immutable bronze file or manifest."""


@dataclass(frozen=True)
class ParsedCsv:
    columns: list[str]  # bronze column names, in file order
    data: dict[str, list[str | None]]  # column -> values
    rows: int
    source_last_update: str | None


def _parse_last_update(value: str) -> datetime:
    return datetime.strptime(value, "%d/%m/%y %H:%M:%S")  # naive on purpose: Eurostat local time


def parse_and_validate(csv_text: str, dataset_code: str) -> ParsedCsv:
    """Check header + every row, and pivot the CSV to column lists for Arrow."""
    reader = csv.DictReader(io.StringIO(csv_text), restkey="_extra")
    header = list(reader.fieldnames or [])
    check_header(header, dataset_code)

    columns = [bronze_column_name(h) for h in header]
    data: dict[str, list[str | None]] = {c: [] for c in columns}
    errors: list[str] = []
    error_count = 0
    last_updates: list[str] = []

    for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
        problem: str | None = None
        if "_extra" in raw:
            problem = "row has more fields than the header"
        else:
            row = {bronze_column_name(k): v for k, v in raw.items()}
            try:
                RowCheck.model_validate(row)
            except ValidationError as exc:
                problem = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
        if problem:
            error_count += 1
            if len(errors) < _MAX_ERRORS_REPORTED:
                errors.append(f"line {line_no}: {problem}")
            continue
        for header_name, col in zip(header, columns, strict=True):
            value = raw[header_name]
            data[col].append(None if (col in _NULLABLE_WHEN_EMPTY and value == "") else value)
        last_updates.append(raw["LAST UPDATE"])

    if error_count:
        raise DataValidationError(
            f"{dataset_code}: {error_count} invalid row(s). First ones: " + " | ".join(errors)
        )
    if not last_updates:
        raise DataValidationError(f"{dataset_code}: response contains zero data rows")

    # Max by parsed time, not by string: dd/mm/yy sorts wrongly as text.
    newest = max(set(last_updates), key=_parse_last_update)
    return ParsedCsv(columns, data, len(last_updates), newest)


def bronze_path(bronze_dir: Path, dataset_code: str, ingestion_date: str, run_id: str) -> Path:
    return (
        bronze_dir
        / f"dataset={dataset_code}"
        / f"ingestion_date={ingestion_date}"
        / f"{run_id}.parquet"
    )


def _atomic_write(final: Path, write_tmp) -> None:
    """Write to a temp file next to ``final`` then rename, so readers never see half a file."""
    if final.exists():
        raise BronzeExistsError(f"{final} already exists; bronze files are never overwritten")
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{final.name}.tmp")
    try:
        write_tmp(tmp)
        # Re-check right before the rename to shrink the race window between two writers.
        if final.exists():
            raise BronzeExistsError(f"{final} already exists; bronze files are never overwritten")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)


def write_bronze_file(
    parsed: ParsedCsv,
    *,
    bronze_dir: Path,
    dataset_code: str,
    run_id: str,
    ingestion_date: str,
    ingested_at: datetime,
    source_url: str,
) -> Path:
    """Write one dataset snapshot. Source columns + the four ``_`` metadata columns."""
    n = parsed.rows
    arrays: dict[str, pa.Array] = {
        col: pa.array(values, type=pa.string()) for col, values in parsed.data.items()
    }
    arrays["_dataset_code"] = pa.array([dataset_code] * n, type=pa.string())
    arrays["_run_id"] = pa.array([run_id] * n, type=pa.string())
    arrays["_ingested_at"] = pa.array([ingested_at] * n, type=pa.timestamp("us", tz="UTC"))
    arrays["_source_url"] = pa.array([source_url] * n, type=pa.string())
    table = pa.table(arrays)

    final = bronze_path(bronze_dir, dataset_code, ingestion_date, run_id)
    _atomic_write(final, lambda tmp: pq.write_table(table, tmp, compression="zstd"))
    return final


def manifest_path(bronze_dir: Path, run_id: str) -> Path:
    return bronze_dir / "_runs" / f"{run_id}.json"


def write_manifest(manifest: RunManifest, bronze_dir: Path) -> Path:
    final = manifest_path(bronze_dir, manifest.run_id)
    payload = manifest.model_dump_json(indent=2)
    _atomic_write(final, lambda tmp: tmp.write_text(payload + "\n", encoding="utf-8"))
    return final


def make_run_id(started_at: datetime) -> str:
    return started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
