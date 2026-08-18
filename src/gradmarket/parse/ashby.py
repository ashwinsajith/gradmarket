"""Ashby extractor: normalises raw_fetches payloads into ParsedPosting records."""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash


def _combined_location(job: dict) -> str | None:
    """location is the primary location; secondaryLocations (a list of
    {"location": ..., "address": ...} — verified against a live API response,
    no "allLocations" field exists here) holds the rest. Dropping it means
    every location but the first is lost for multi-location postings."""
    parts: list[str] = []
    seen: set[str] = set()

    primary = job.get("location")
    if primary:
        parts.append(primary)
        seen.add(primary.lower())

    for entry in job.get("secondaryLocations") or []:
        name = entry.get("location") if isinstance(entry, dict) else None
        if name and name.lower() not in seen:
            parts.append(name)
            seen.add(name.lower())

    return "; ".join(parts) if parts else None


def extract(payload: Any) -> list[ParsedPosting]:
    postings = []
    for job in payload.get("jobs", []):
        external_id = job.get("id")
        if external_id is None:
            print(f"warning: ashby job missing id, skipping: {job.get('title')!r}")
            continue

        title = job.get("title")
        location = _combined_location(job)
        department = job.get("department")
        url = job.get("jobUrl")
        description_raw = job.get("descriptionHtml") or job.get("descriptionPlain")

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
