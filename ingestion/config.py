"""Loads and validates ``config/datasets.yaml``.

The path comes from ``common.settings`` so tests and CI can point at another file via the
``EUROPULSE_DATASETS_CONFIG`` environment variable.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from ingestion.schemas import DatasetsFile


class ConfigError(RuntimeError):
    """datasets.yaml is missing or invalid."""


def load_config(path: Path) -> DatasetsFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read dataset config {path}: {exc}") from exc
    try:
        return DatasetsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid dataset config {path}:\n{exc}") from exc
