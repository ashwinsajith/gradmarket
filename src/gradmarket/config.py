"""companies.yaml loading, shared by ingest.py (what to fetch) and
parse_run.py (what's still configured, for orphan reconciliation).

Neither stage owns this — same reasoning as health.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def resolve_companies_file() -> Path:
    env_value = os.environ.get("COMPANIES_FILE")
    path = Path(env_value) if env_value else Path.cwd() / "companies.yaml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"companies file not found: {path}")
    return path


def load_companies(path: Path) -> dict[str, list[str]]:
    with path.open() as f:
        return yaml.safe_load(f) or {}
