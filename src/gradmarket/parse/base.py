"""Shared normalised return type for gradmarket.parse extractors.

Every source's extractor exposes extract(payload) -> list[ParsedPosting] and
a module-level NEEDS_DETAILS: bool flag, so parse_run.py can treat all
sources identically, with no provider-specific logic of its own.

NEEDS_DETAILS is False for every source whose raw_fetches payload alone is
enough to extract a full ParsedPosting (Greenhouse, Lever, Ashby, Workable):
their extract(payload) signature never changes. It's True for a two-stage
source like Workday, whose extract(payload, context) takes an extra
ExtractorContext built by parse_run.process_row — see its own module
docstring for why one posting's data lives in two separate raw tables there.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass
class ParsedPosting:
    external_id: str
    title: str | None
    location: str | None
    department: str | None
    url: str | None
    description_raw: str | None
    content_hash: str


@dataclass
class ExtractorContext:
    """Extra data a two-stage extractor (NEEDS_DETAILS = True) needs beyond
    its raw_fetches payload. Built generically by parse_run.process_row —
    it has no source-specific knowledge of its own, just the raw materials
    every two-stage extractor needs so far: that (source, company)'s
    raw_details keyed by external_id (db.get_raw_details_by_external_id),
    and its companies.yaml token (whatever type that source's config
    entries are — see gradmarket.config.CompanyToken). An extractor that
    doesn't need one of these just ignores it."""

    details_by_external_id: dict[str, Any]
    token: Any


def compute_content_hash(title: str | None, location: str | None, description_raw: str | None) -> str:
    """Hash of exactly the fields posting_versions tracks, so a version is
    appended only when one of them actually changes."""
    parts = [title or "", location or "", description_raw or ""]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
