# 0006 — uv for environments, Ruff for lint and formatting

- **Status:** accepted
- **Date:** 2026-10-28

## Context
The project plan suggested pre-commit with ruff and black. Reproducibility
("clone, one command, it runs") needs a lockfile.

## Decision
Use `uv` with `pyproject.toml` dependency groups and a committed `uv.lock`; use
Ruff for both linting and formatting (`ruff format` is a Black-compatible drop-in,
one tool instead of two).

## Consequences
- `uv sync` reproduces the exact environment in CI, Docker and on Windows.
- Airflow is not in the uv environment: it runs in its own Docker image, because
  its constraint-pinned dependency tree conflicts with a modern app stack.
