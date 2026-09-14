"""Workday extractor: normalises raw_fetches + raw_details payloads into
ParsedPosting records.

Unlike every other source, one Workday posting's data lives in two places:
the list payload (gradmarket.sources.workday.fetch, stored in raw_fetches)
gives identity and title; the per-posting detail payload
(gradmarket.sources.workday.fetch_detail, stored in raw_details) gives
description and location. extract() therefore takes both, plus the
WorkdayToken needed to build each posting's public URL from its
externalPath (list items don't carry a hostedUrl/absolute_url of their own
the way every other source does).

external_id is bulletFields[0] when present, falling back to externalPath —
bulletFields is tenant-configurable in Workday, so a tenant that doesn't
populate it must still get a stable identifier. externalPath is kept as a
separate field regardless (both to build the URL and because
detail_run.py needs it to call fetch_detail), never folded away just
because it was also used as the external_id fallback.

Location is built country-first — "United Kingdom, Edinburgh" — by
prepending jobPostingInfo.country.descriptor (when present) to
jobPostingInfo.location plus jobPostingInfo.additionalLocations joined with
"; ". This is the opposite order from every other source's location
strings and is a real, observed Workday quirk, not a bug to normalise away
here — parse.workday is a faithful extractor, not a display formatter.

Handling a detail that hasn't been fetched yet (detail_run.py runs as a
separate, later pass — see its docstring): such a posting still gets a
ParsedPosting, built from the list item alone (external_id, title, url),
with location and description_raw left None. The alternative — skipping
the posting entirely until its detail arrives — would delay first_seen_at
for every Workday posting by however long the detail backfill takes, and
would permanently hide any posting whose detail-fetch fails outright.
content_hash is computed over whatever fields are known at the time, so
the day a detail payload does show up, the hash changes and a new
posting_versions row is appended, without any special-casing here.
"""

from __future__ import annotations

from typing import Any

from gradmarket.parse.base import ParsedPosting, compute_content_hash
from gradmarket.sources.workday import WorkdayToken

PUBLIC_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{external_path}"


def _external_id(item: dict) -> str | None:
    bullet_fields = item.get("bulletFields") or []
    if bullet_fields and bullet_fields[0]:
        return str(bullet_fields[0])
    external_path = item.get("externalPath")
    return str(external_path) if external_path else None


def _posting_url(token: WorkdayToken, external_path: str) -> str:
    return PUBLIC_URL_TEMPLATE.format(tenant=token.tenant, dc=token.dc, site=token.site, external_path=external_path)


def _location_text(job_posting_info: dict) -> str | None:
    primary = job_posting_info.get("location")
    additional = job_posting_info.get("additionalLocations") or []
    locations = [loc for loc in [primary, *additional] if loc]
    joined = "; ".join(locations)
    country_descriptor = (job_posting_info.get("country") or {}).get("descriptor")
    if country_descriptor and joined:
        return f"{country_descriptor}, {joined}"
    return country_descriptor or joined or None


def extract(
    list_payload: Any,
    details_by_external_id: dict[str, Any],
    token: WorkdayToken,
) -> list[ParsedPosting]:
    """list_payload is the bare list gradmarket.sources.workday.fetch()
    returns (raw_fetches.payload for this source). details_by_external_id
    maps external_id -> the stripped detail JSON gradmarket.sources.workday.
    fetch_detail() returns (raw_details.payload); an external_id with no
    entry means its detail hasn't been fetched yet — see module docstring.
    """
    postings = []
    for item in list_payload:
        external_path = item.get("externalPath")
        if not external_path:
            print(f"warning: workday/{token.company} job missing externalPath, skipping: {item.get('title')!r}")
            continue

        external_id = _external_id(item)
        if external_id is None:
            print(f"warning: workday/{token.company} job missing external_id, skipping: {item.get('title')!r}")
            continue

        list_title = item.get("title")
        url = _posting_url(token, external_path)
        detail = details_by_external_id.get(external_id)

        if detail is None:
            postings.append(
                ParsedPosting(
                    external_id=external_id,
                    title=list_title,
                    location=None,
                    department=None,
                    url=url,
                    description_raw=None,
                    content_hash=compute_content_hash(list_title, None, None),
                )
            )
            continue

        job_posting_info = detail.get("jobPostingInfo") or {}
        title = list_title or job_posting_info.get("title")
        location = _location_text(job_posting_info)
        description_raw = job_posting_info.get("jobDescription")

        postings.append(
            ParsedPosting(
                external_id=external_id,
                title=title,
                location=location,
                department=None,
                url=url,
                description_raw=description_raw,
                content_hash=compute_content_hash(title, location, description_raw),
            )
        )
    return postings
