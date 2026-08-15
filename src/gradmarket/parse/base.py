"""Shared normalised return type for gradmarket.parse extractors.

Every source's extractor exposes extract(payload) -> list[ParsedPosting] so
parse_run.py can treat all sources identically, with no provider-specific
logic of its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class ParsedPosting:
    external_id: str
    title: str | None
    location: str | None
    department: str | None
    url: str | None
    description_raw: str | None
    content_hash: str


def compute_content_hash(title: str | None, location: str | None, description_raw: str | None) -> str:
    """Hash of exactly the fields posting_versions tracks, so a version is
    appended only when one of them actually changes."""
    parts = [title or "", location or "", description_raw or ""]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
