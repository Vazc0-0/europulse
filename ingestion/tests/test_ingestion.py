"""Tests for the ingestion layer. HTTP is mocked with respx; no network needed."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import duckdb
import httpx
import pyarrow.parquet as pq
import pytest
import respx

from ingestion.__main__ import main
from ingestion.bronze import BronzeExistsError, parse_and_validate
from ingestion.config import ConfigError, load_config
from ingestion.eurostat_client import (
    USER_AGENT,
    EurostatAPIError,
    EurostatClient,
    EurostatTransientError,
    build_url,
)
from ingestion.freshness import check_freshness
from ingestion.pipeline import load_fixtures, run_ingestion
from ingestion.schemas import DataValidationError
from ingestion.tests.conftest import BASE_URL, fixture_csv, make_clock

CODES = ["prc_hicp_minr", "une_rt_m", "nrg_pc_204"]


def mock_all(csv_for=fixture_csv):
    """Mock every configured dataset URL with its fixture CSV."""
    for code in CODES:
        respx.get(url__regex=rf"{BASE_URL}/{code}/.*").mock(
            return_value=httpx.Response(200, text=csv_for(code))
        )


# --- URL building -----------------------------------------------------------------------


def test_build_url_joins_geos_in_key(config):
    url = build_url(config.base_url, config.datasets[0], config.geos)
    assert (
        url == f"{BASE_URL}/prc_hicp_minr/M.RCH_A.TOTAL.PT+EL?format=SDMX-CSV&startPeriod=2019-01"
    )
    assert "geo=" not in url.split("?")[1]  # filters must live in the key, not the query


def test_build_url_semiannual_start_period(config):
    url = build_url(config.base_url, config.datasets[2], config.geos)
    assert url.endswith(
        "/S.E7000.KWH2500-4999.KWH.I_TAX.EUR.PT+EL?format=SDMX-CSV&startPeriod=2019-S1"
    )


def test_real_config_file_is_valid():
    from common.settings import get_settings

    cfg = load_config(get_settings().datasets_config)
    assert [d.code for d in cfg.datasets] == CODES
    assert len(cfg.geos) == 28


def test_invalid_config_raises(tmp_path):
    bad = tmp_path / "d.yaml"
    bad.write_text(
        "base_url: x\ngeos: [PT]\ndatasets:\n"
        "  a: {key: 'no placeholder', start_period: '2019-01', frequency: monthly}\n"
    )
    with pytest.raises(ConfigError):
        load_config(bad)


# --- happy path -------------------------------------------------------------------------


@respx.mock
def test_run_writes_bronze_at_contract_path(config, settings, clock):
    mock_all()
    manifest = run_ingestion(config, settings, clock=clock)

    assert manifest.status == "success"
    assert manifest.run_id == "20261028T231500Z"
    for code in CODES:
        path = (
            settings.bronze_dir
            / f"dataset={code}/ingestion_date=2026-10-28/20261028T231500Z.parquet"
        )
        assert path.exists()
        assert manifest.datasets[code].file == path.relative_to(settings.data_dir).as_posix()

    table = pq.read_table(
        settings.bronze_dir / "dataset=une_rt_m/ingestion_date=2026-10-28/20261028T231500Z.parquet"
    )
    assert table.column_names == [
        "dataflow", "last_update", "freq", "s_adj", "age", "unit", "sex", "geo",
        "time_period", "obs_value", "obs_flag", "conf_status",
        "_dataset_code", "_run_id", "_ingested_at", "_source_url",
    ]  # fmt: skip
    schema = table.schema
    assert all(
        str(schema.field(n).type) == "string" for n in table.column_names if n != "_ingested_at"
    )
    assert str(schema.field("_ingested_at").type) == "timestamp[us, tz=UTC]"

    row = table.slice(0, 1).to_pylist()[0]
    assert row["_dataset_code"] == "une_rt_m"
    assert row["_run_id"] == "20261028T231500Z"
    assert row["_source_url"].startswith(
        f"{BASE_URL}/une_rt_m/M.SA.TOTAL.PC_ACT.T.PT+EL?format=SDMX-CSV"
    )
    assert row["_ingested_at"].tzinfo is not None
    assert row["obs_flag"] is None and row["conf_status"] is None  # empty -> NULL
    assert row["last_update"] == "21/10/26 11:00:00"
    assert manifest.datasets["une_rt_m"].rows == table.num_rows
    assert manifest.datasets["une_rt_m"].source_last_update == "21/10/26 11:00:00"

    on_disk = json.loads((settings.bronze_dir / "_runs/20261028T231500Z.json").read_text())
    assert on_disk["status"] == "success" and set(on_disk["datasets"]) == set(CODES)


@respx.mock
def test_request_sends_user_agent(config, settings, clock):
    route = respx.get(url__regex=rf"{BASE_URL}/une_rt_m/.*").mock(
        return_value=httpx.Response(200, text=fixture_csv("une_rt_m"))
    )
    run_ingestion(config, settings, only=["une_rt_m"], clock=clock)
    assert route.calls.last.request.headers["user-agent"] == USER_AGENT


@respx.mock
def test_only_filters_datasets(config, settings, clock):
    mock_all()
    manifest = run_ingestion(config, settings, only=["une_rt_m"], clock=clock)
    assert list(manifest.datasets) == ["une_rt_m"]


@respx.mock
def test_running_twice_keeps_both_files(config, settings):
    mock_all()
    first = run_ingestion(
        config, settings, clock=make_clock(datetime(2026, 10, 28, 8, 0, tzinfo=UTC))
    )
    second = run_ingestion(
        config, settings, clock=make_clock(datetime(2026, 10, 28, 20, 0, tzinfo=UTC))
    )
    assert first.run_id != second.run_id
    files = sorted(settings.bronze_dir.glob("dataset=*/ingestion_date=*/*.parquet"))
    assert len(files) == 6
    assert len(list((settings.bronze_dir / "_runs").glob("*.json"))) == 2


@respx.mock
def test_same_run_id_never_overwrites(config, settings):
    mock_all()
    start = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
    run_ingestion(config, settings, clock=make_clock(start))
    path = (
        settings.bronze_dir / "dataset=une_rt_m/ingestion_date=2026-10-28/20261028T080000Z.parquet"
    )
    before = path.read_bytes()
    # Same clock -> same run_id: every dataset must fail instead of overwriting, and the
    # manifest write itself is refused.
    with pytest.raises(BronzeExistsError):
        run_ingestion(config, settings, clock=make_clock(start))
    assert path.read_bytes() == before


# --- retries and errors -----------------------------------------------------------------


def _client() -> EurostatClient:
    return EurostatClient(
        BASE_URL, timeout=5, max_attempts=3, backoff_initial=0.001, backoff_max=0.01
    )


@respx.mock
def test_retry_503_then_200(config):
    route = respx.get(url__regex=rf"{BASE_URL}/une_rt_m/.*").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="ok")]
    )
    with _client() as client:
        result = client.fetch_dataset(config.datasets[1], config.geos)
    assert result.text == "ok" and route.call_count == 2


@respx.mock
def test_retry_on_timeout_and_429(config):
    route = respx.get(url__regex=rf"{BASE_URL}/une_rt_m/.*").mock(
        side_effect=[
            httpx.ConnectTimeout("slow"),
            httpx.Response(429),
            httpx.Response(200, text="ok"),
        ]
    )
    with _client() as client:
        assert client.fetch_dataset(config.datasets[1], config.geos).text == "ok"
    assert route.call_count == 3


@respx.mock
def test_gives_up_after_max_attempts(config):
    route = respx.get(url__regex=rf"{BASE_URL}/une_rt_m/.*").mock(return_value=httpx.Response(500))
    with _client() as client, pytest.raises(EurostatTransientError):
        client.fetch_dataset(config.datasets[1], config.geos)
    assert route.call_count == 3


@respx.mock
def test_400_not_retried_and_message_has_eurostat_label(config):
    body = {"error": [{"status": 400, "label": "Dimension geo has invalid value XX"}]}
    route = respx.get(url__regex=rf"{BASE_URL}/une_rt_m/.*").mock(
        return_value=httpx.Response(400, json=body)
    )
    with _client() as client, pytest.raises(EurostatAPIError) as err:
        client.fetch_dataset(config.datasets[1], config.geos)
    assert route.call_count == 1
    assert "Dimension geo has invalid value XX" in str(err.value)
    assert "HTTP 400" in str(err.value)


# --- validation -------------------------------------------------------------------------


@respx.mock
def test_missing_column_fails_one_dataset_only(config, settings, clock):
    def csv_for(code):
        text = fixture_csv(code)
        if code == "une_rt_m":  # drop CONF_STATUS from header and rows
            text = "\n".join(line.rsplit(",", 1)[0] for line in text.splitlines()) + "\n"
        return text

    mock_all(csv_for)
    manifest = run_ingestion(config, settings, clock=clock)

    assert manifest.status == "partial"
    assert manifest.datasets["une_rt_m"].status == "failed"
    assert "CONF_STATUS" in manifest.datasets["une_rt_m"].error
    assert manifest.datasets["prc_hicp_minr"].status == "success"
    assert not list(settings.bronze_dir.glob("dataset=une_rt_m/**/*.parquet"))
    assert len(list(settings.bronze_dir.glob("dataset=*/**/*.parquet"))) == 2


@respx.mock
def test_all_failed_status(config, settings, clock):
    respx.get(url__regex=rf"{BASE_URL}/.*").mock(
        return_value=httpx.Response(400, json={"error": []})
    )
    manifest = run_ingestion(config, settings, clock=clock)
    assert manifest.status == "failed"
    assert (settings.bronze_dir / f"_runs/{manifest.run_id}.json").exists()


def test_bad_time_period_rejected():
    text = fixture_csv("une_rt_m").replace(",2025-01,", ",Jan-2025,", 1)
    with pytest.raises(DataValidationError, match="time_period"):
        parse_and_validate(text, "une_rt_m")


def test_bad_obs_value_and_empty_geo_rejected():
    lines = fixture_csv("une_rt_m").splitlines()
    cells = lines[1].split(",")
    cells[9] = "abc"
    lines[1] = ",".join(cells)
    cells = lines[2].split(",")
    cells[7] = ""
    lines[2] = ",".join(cells)
    with pytest.raises(DataValidationError, match="2 invalid row"):
        parse_and_validate("\n".join(lines), "une_rt_m")


def test_empty_obs_value_is_allowed_and_kept_as_empty_text():
    lines = fixture_csv("une_rt_m").splitlines()[:2]
    cells = lines[1].split(",")
    cells[9], cells[10] = "", "e"
    parsed = parse_and_validate("\n".join([lines[0], ",".join(cells)]), "une_rt_m")
    assert parsed.data["obs_value"] == [""]
    assert parsed.data["obs_flag"] == ["e"]


def test_zero_rows_is_an_error():
    header = fixture_csv("une_rt_m").splitlines()[0]
    with pytest.raises(DataValidationError, match="zero data rows"):
        parse_and_validate(header + "\n", "une_rt_m")


# --- freshness --------------------------------------------------------------------------


@respx.mock
def test_freshness_fresh_and_stale(config, settings):
    mock_all()
    started = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
    run_ingestion(config, settings, clock=make_clock(started))

    fresh = check_freshness(settings.bronze_dir, 36, now=started + timedelta(hours=10))
    assert fresh.ok and fresh.run_id == "20261028T080000Z"
    stale = check_freshness(settings.bronze_dir, 36, now=started + timedelta(hours=40))
    assert not stale.ok and "STALE" in stale.message


def test_freshness_without_manifests_is_stale(settings):
    report = check_freshness(settings.bronze_dir, 36)
    assert not report.ok and "no successful run" in report.message


@respx.mock
def test_freshness_ignores_partial_runs(config, settings, clock):
    respx.get(url__regex=rf"{BASE_URL}/prc_hicp_minr/.*").mock(
        return_value=httpx.Response(200, text=fixture_csv("prc_hicp_minr"))
    )
    respx.get(url__regex=rf"{BASE_URL}/(une_rt_m|nrg_pc_204)/.*").mock(
        return_value=httpx.Response(400)
    )
    assert run_ingestion(config, settings, clock=clock).status == "partial"
    assert not check_freshness(settings.bronze_dir, 36).ok


@respx.mock
def test_freshness_cli_exit_codes(config, settings, monkeypatch, capsys):
    data_dir = str(settings.data_dir)
    assert main(["freshness", "--max-age-hours", "36", "--data-dir", data_dir]) == 1
    # A manifest from "now" (real clock) is fresh.
    mock_all()
    run_ingestion(config, settings)
    assert main(["freshness", "--max-age-hours", "36", "--data-dir", data_dir]) == 0
    assert "FRESH" in capsys.readouterr().out


# --- load-fixtures ----------------------------------------------------------------------


def test_load_fixtures_writes_three_files(config, settings, clock):
    manifest = load_fixtures(config, settings, clock=clock)
    assert manifest.status == "success"
    files = list(settings.bronze_dir.glob("dataset=*/ingestion_date=*/*.parquet"))
    assert len(files) == 3
    # dbt will read them with hive partitioning; make sure DuckDB agrees with the contract.
    result = duckdb.sql(
        "select _dataset_code, count(*) "
        f"from read_parquet('{settings.bronze_dir}/**/*.parquet') group by 1"
    ).fetchall()
    assert {r[0] for r in result} == set(CODES)


def test_load_fixtures_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("EUROPULSE_DATA_DIR", str(tmp_path))
    from common.settings import get_settings

    get_settings.cache_clear()
    try:
        assert main(["load-fixtures", "--data-dir", str(tmp_path / "d")]) == 0
    finally:
        get_settings.cache_clear()
    assert len(list((tmp_path / "d").glob("bronze/eurostat/dataset=*/*/*.parquet"))) == 3


def test_run_cli_rejects_unknown_dataset(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["run", "--dataset", "nope", "--data-dir", str(tmp_path)])
    assert exc.value.code == 2


# --- live -------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("EUROPULSE_LIVE_TESTS") != "1", reason="set EUROPULSE_LIVE_TESTS=1"
)
def test_live_one_dataset(tmp_path):
    from common.settings import Settings, get_settings

    settings = Settings(data_dir=tmp_path)
    config = load_config(get_settings().datasets_config)
    manifest = run_ingestion(config, settings, only=["une_rt_m"])
    assert manifest.status == "success"
    assert manifest.datasets["une_rt_m"].rows > 1000
