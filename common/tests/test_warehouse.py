"""Tests for the serving-copy publish and read-only access (ADR 0003)."""

import duckdb
import pytest

from common.warehouse import (
    WarehouseNotReadyError,
    publish_serving_copy,
    read_only_connection,
)


def test_publish_then_read_only(tmp_path):
    source = tmp_path / "europulse.duckdb"
    target = tmp_path / "europulse_serving.duckdb"
    with duckdb.connect(str(source)) as con:
        con.execute("CREATE SCHEMA marts; CREATE TABLE marts.t AS SELECT 42 AS x")

    publish_serving_copy(source, target)

    with read_only_connection(target) as con:
        assert con.execute("SELECT x FROM marts.t").fetchone() == (42,)
        # Writes must be impossible on the serving copy.
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE marts.evil AS SELECT 1")


def test_missing_serving_copy_raises(tmp_path):
    with pytest.raises(WarehouseNotReadyError), read_only_connection(tmp_path / "nope.duckdb"):
        pass
