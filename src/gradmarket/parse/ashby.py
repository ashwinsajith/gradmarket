"""Ashby extractor: normalises raw_fetches payloads into ParsedPosting records."""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash


def extract(payload: Any) -> list[ParsedPosting]:
    postings = []
    for job in payload.get("jobs", []):
        external_id = job.get("id")
        if external_id is None:
            print(f"warning: ashby job missing id, skipping: {job.get('title')!r}")
            continue

        title = job.get("title")
        location = job.get("location")
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
