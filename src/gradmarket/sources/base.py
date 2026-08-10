"""Shared normalised return type for gradmarket.sources modules.

Every source module exposes fetch(token: str) -> FetchResult so ingest.py can
treat all sources identically, with no provider-specific logic of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FetchResult:
    status_code: int | None
    payload: Any | None
    job_count: int
    error: str | None = None
