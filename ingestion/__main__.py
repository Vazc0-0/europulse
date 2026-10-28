"""Command line entry point: ``python -m ingestion <command>``.

Commands:
    run              download datasets from Eurostat into bronze
    freshness        exit 1 if the newest successful run is too old (or missing)
    load-fixtures    write bronze from saved CSV fixtures (offline, for CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.settings import Settings, get_settings
from ingestion.bronze import manifest_path
from ingestion.config import ConfigError, load_config
from ingestion.freshness import check_freshness
from ingestion.logging_config import configure_logging
from ingestion.pipeline import load_fixtures, run_ingestion


def _settings(data_dir: Path | None) -> Settings:
    settings = get_settings()
    # model_copy keeps every other setting (env vars, .env) and only swaps the data dir.
    return settings.model_copy(update={"data_dir": data_dir}) if data_dir else settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="download datasets from Eurostat")
    run.add_argument(
        "--dataset", action="append", metavar="CODE", help="only this dataset (repeatable)"
    )
    run.add_argument("--data-dir", type=Path, help="override EUROPULSE_DATA_DIR")

    fresh = sub.add_parser("freshness", help="check the age of the newest successful run")
    fresh.add_argument("--max-age-hours", type=float, default=36.0)
    fresh.add_argument("--data-dir", type=Path, help="override EUROPULSE_DATA_DIR")

    fixtures = sub.add_parser("load-fixtures", help="write bronze from test fixtures (no network)")
    fixtures.add_argument("--data-dir", type=Path, help="override EUROPULSE_DATA_DIR")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    settings = _settings(args.data_dir)

    if args.command == "freshness":
        report = check_freshness(settings.bronze_dir, args.max_age_hours)
        print(report.message)
        return 0 if report.ok else 1

    try:
        config = load_config(settings.datasets_config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.command == "run":
        known = {d.code for d in config.datasets}
        only = [c.lower() for c in args.dataset or []]
        unknown = sorted(set(only) - known)
        if unknown:
            parser.error(f"unknown dataset(s) {unknown}; configured: {sorted(known)}")
        manifest = run_ingestion(config, settings, only=only or None)
    else:
        manifest = load_fixtures(config, settings)

    path = manifest_path(settings.bronze_dir, manifest.run_id)
    print(f"run {manifest.run_id}: {manifest.status} -> {path}")
    return 0 if manifest.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
