"""Lever extractor: normalises raw_fetches payloads into ParsedPosting records.

payload is a bare JSON array (the concatenation of paginated pages written by
gradmarket.sources.lever), not a {"jobs": [...]} object like Greenhouse/Ashby.
"""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash


def _combined_location(categories: dict) -> str | None:
    """categories.allLocations already includes the primary location — a list
    of plain strings, verified against live data, not assumed — so prefer it
    over categories.location alone. Falls back to location for payloads
    without allLocations."""
    all_locations = categories.get("allLocations")
    if all_locations:
        deduped = list(dict.fromkeys(loc for loc in all_locations if loc))
        if deduped:
            return "; ".join(deduped)
    return categories.get("location")


def extract(payload: Any) -> list[ParsedPosting]:
    postings = []
    for job in payload:
        external_id = job.get("id")
        if external_id is None:
            print(f"warning: lever job missing id, skipping: {job.get('text')!r}")
            continue

        title = job.get("text")
        categories = job.get("categories") or {}
        location = _combined_location(categories)
        department = categories.get("department") or categories.get("team")
        url = job.get("hostedUrl")
        description_raw = job.get("description")

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
