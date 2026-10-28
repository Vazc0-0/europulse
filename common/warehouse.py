"""Access to the DuckDB warehouse, shared by the API, the dashboard and the AI layer.

Why two database files?
DuckDB allows one writer *or* many readers per file across processes. If the API
held the warehouse open, the nightly ``dbt build`` would fail with a lock error
(and vice versa). So dbt writes ``europulse.duckdb`` and, when the build is green,
``publish_serving_copy`` atomically swaps in ``europulse_serving.duckdb``. Every
consumer reads only that copy, only in read-only mode, and only the ``marts``
schema (the gold layer). See docs/adr/0003-serving-copy.md.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from common.settings import get_settings

GOLD_SCHEMA = "marts"


class WarehouseNotReadyError(RuntimeError):
    """Raised when no serving copy exists yet (the pipeline has never run)."""


def publish_serving_copy(source: Path | None = None, target: Path | None = None) -> Path:
    """Checkpoint the pipeline warehouse and atomically replace the serving copy.

    The copy is written to a temp file next to the target and moved into place
    with ``os.replace`` (atomic on the same volume), so readers never see a
    half-written file. On Windows the replace fails while a reader has the file
    open, so we retry briefly; readers use short-lived connections.
    """
    settings = get_settings()
    source = source or settings.warehouse_path
    target = target or settings.serving_path
    if not source.exists():
        raise FileNotFoundError(f"Warehouse not found: {source}. Run dbt build first.")

    # Flush the write-ahead log into the main file so the copy is self-contained.
    with duckdb.connect(str(source)) as con:
        con.execute("CHECKPOINT")

    tmp = target.with_suffix(".duckdb.tmp")
    shutil.copyfile(source, tmp)
    for attempt in range(10):
        try:
            os.replace(tmp, target)
            return target
        except PermissionError:  # pragma: no cover - Windows-only race
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Could not replace {target}: file kept locked by a reader.")


@contextmanager
def read_only_connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a short-lived, read-only connection to the serving copy.

    ``read_only=True`` is enforced by DuckDB itself: any INSERT/UPDATE/DDL fails,
    which is the last line of defence for the AI text-to-SQL layer.
    """
    path = path or get_settings().serving_path
    if not path.exists():
        raise WarehouseNotReadyError(
            f"Serving database not found at {path}. Run the pipeline first (make run-pipeline)."
        )
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()
