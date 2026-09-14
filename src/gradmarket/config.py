"""companies.yaml loading, shared by ingest.py (what to fetch) and
parse_run.py (what's still configured, for orphan reconciliation).

Neither stage owns this — same reasoning as health.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from gradmarket.sources.workday import WorkdayToken

CompanyToken = str | WorkdayToken


def resolve_companies_file() -> Path:
    env_value = os.environ.get("COMPANIES_FILE")
    path = Path(env_value) if env_value else Path.cwd() / "companies.yaml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"companies file not found: {path}")
    return path


def _build_token(source_name: str, entry: Any) -> CompanyToken:
    """Every source's companies.yaml entries are a bare string token, except
    Workday's — it needs three coordinates (tenant/dc/site) instead of one,
    so its entries are objects, not strings (see CLAUDE.md's data model
    notes). This is the one place that difference gets resolved; every
    reader downstream (ingest.py included) just gets back whichever token
    type the source actually uses, with no branching of its own."""
    if source_name == "workday":
        return WorkdayToken(**entry)
    return entry


def load_companies(path: Path) -> dict[str, list[CompanyToken]]:
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    return {source_name: [_build_token(source_name, entry) for entry in entries] for source_name, entries in raw.items()}
