"""Workable extractor: normalises raw_fetches payloads into ParsedPosting records.

Workable jobs have no "id" field at all — shortcode is the unique
identifier and is what external_id maps to for this source (see CLAUDE.md
data model notes).
"""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash


def _location_text(entry: dict) -> str | None:
    city = entry.get("city")
    country = entry.get("country")
    if city and country:
        return f"{city}, {country}"
    return city or country or entry.get("region") or None


def _combined_location(job: dict) -> str | None:
    """locations is a list of {city, country, countryCode, region, hidden}
    entries. Every live posting checked during verification had exactly one
    entry, but Workable's own docs confirm up to 6 locations per posting is
    a real, supported feature — join every entry rather than reading just
    locations[0] or the redundant singular city/country/state fields at the
    job's top level (which always mirror locations[0]). Pre-emptive fix for
    the same bug Ashby had: dropping every location but the first.

    "hidden" only controls visibility on Workable's own careers page — we
    store raw data, not a published-page filter, so hidden entries are
    still included.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for entry in job.get("locations") or []:
        text = _location_text(entry)
        if text and text.lower() not in seen:
            parts.append(text)
            seen.add(text.lower())
    return "; ".join(parts) if parts else None


def extract(payload: Any) -> list[ParsedPosting]:
    postings = []
    for job in payload.get("jobs", []):
        external_id = job.get("shortcode")
        if external_id is None:
            print(f"warning: workable job missing shortcode, skipping: {job.get('title')!r}")
            continue

        title = job.get("title")
        location = _combined_location(job)
        department = job.get("department")
        url = job.get("url")
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
