"""Greenhouse extractor: normalises raw_fetches payloads into ParsedPosting records."""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash


def _combined_location(job: dict) -> str | None:
    """location.name is free text and often incomplete on its own — verified
    against live data: sometimes it's vague ("Hybrid", "US") while offices
    holds the real cities, sometimes it's already a multi-location string
    ("London; Remote (UK)") that offices doesn't add anything to, and offices
    names are themselves inconsistent (city names, region buckets, or
    internal codes). Neither source is reliably complete alone, so keep
    location.name as-is and append any office name not already represented
    in it, rather than picking one source over the other."""
    parts: list[str] = []

    primary = (job.get("location") or {}).get("name")
    if primary:
        parts.append(primary)

    combined_lower = (primary or "").lower()
    for office in job.get("offices") or []:
        name = office.get("name")
        if not name or name.lower() in combined_lower:
            continue
        parts.append(name)
        combined_lower += "; " + name.lower()

    return "; ".join(parts) if parts else None


def extract(payload: Any) -> list[ParsedPosting]:
    postings = []
    for job in payload.get("jobs", []):
        external_id = job.get("id")
        if external_id is None:
            print(f"warning: greenhouse job missing id, skipping: {job.get('title')!r}")
            continue

        title = job.get("title")
        location = _combined_location(job)
        departments = job.get("departments") or []
        department = departments[0].get("name") if departments else None
        url = job.get("absolute_url")
        description_raw = job.get("content")

        postings.append(
            ParsedPosting(
                external_id=str(external_id),
                title=title,
                location=location,
                department=department,
                url=url,
                description_raw=description_raw,
                content_hash=compute_content_hash(title, location, description_raw),
            )
        )
    return postings
