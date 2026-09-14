"""Workday job board source.

Unlike Greenhouse/Lever/Ashby/Workable, a Workday board isn't identified by
one bare token — it needs three coordinates: tenant, datacenter (dc), and
site. companies.yaml expresses a Workday entry as an object with company/
tenant/dc/site fields rather than a bare string; WorkdayToken bundles those
into the single `token` argument so fetch()'s call shape still matches every
other source's fetch(token) -> FetchResult. Datacenter is NOT limited to a
known set like wd1/wd3/wd5 — livenation uses wd503 — so it's taken as given,
never validated against a fixed list.

fetch(token) hits the CXS list endpoint:
    POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
paginated via limit/offset in the JSON body. Workday caps limit at 20 —
asking for more doesn't error, it silently returns an empty array — so
PAGE_LIMIT is hardcoded at 20 rather than tuned up. fetch() also treats an
unexpectedly empty page as end-of-results (rather than trusting `total` and
looping forever) for the same reason: a wrong assumption about the API here
fails silently, not loudly. Pagination is capped at MAX_PAGES like Lever's,
so a runaway board is recorded as a failure rather than returning truncated
data.

A bad site 404s cleanly, with an errorCode and a message naming
Job_Posting_Site_ID — that's a permanent misconfiguration (wrong `site` in
companies.yaml), not a transient failure, so it isn't retried.

fetch_detail(tenant, dc, site, external_path) is separate: a GET to the same
CXS base plus the posting's externalPath, returning the full detail JSON for
one posting. Requires Accept: application/json explicitly — without it,
Workday serves back an HTML shell instead of JSON.

Deliberate exception to storing raw responses untouched (see CLAUDE.md's
"raw first, parse later" rule): fetch_detail() strips any videoInfo block
before returning. videoInfo carries CDN tokens with expiry timestamps that
rotate on every single fetch, which would make the stored payload for an
unchanged posting look different every day — pure noise in raw_details with
no informational value, and it would defeat any future content-hash
comparison over detail payloads. Stripped recursively (rather than at one
assumed nesting depth) since the exact shape/depth of Workday's detail JSON
isn't documented and shouldn't be guessed at with a single hardcoded path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from gradmarket.sources.base import FetchResult

LIST_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
DETAIL_URL_TEMPLATE = "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{external_path}"

LIST_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "en-US",
}
# Accept is required here too — without it Workday returns an HTML shell
# instead of the JSON detail payload.
DETAIL_HEADERS = {
    "Accept": "application/json",
}

# Workday's own ceiling — requesting more doesn't error, it silently returns
# an empty array. Do not raise this.
PAGE_LIMIT = 20
# 20/page * MAX_PAGES = 5000 postings ceiling, same order of magnitude as
# Lever's 100/page * 50 pages.
MAX_PAGES = 250

TIMEOUT = 30
USER_AGENT = "gradmarket-ingest/0.1 (+https://github.com/ashwin-sajith/gradmarket)"
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]
INTER_PAGE_SLEEP = 1
INTER_REQUEST_SLEEP = 1  # between companies, read by ingest.py once this source is wired in


@dataclass(frozen=True)
class WorkdayToken:
    """The `token` gradmarket.sources.workday.fetch() takes. A bare string
    can't express Workday's three-part board identity, so this is the
    richer stand-in — same fetch(token) -> FetchResult call shape as every
    other source, just a structured token instead of a plain str.

    `company` is the same identity slug every other source's bare token
    plays double duty as (what lands in raw_fetches.company /
    postings.company); it isn't necessarily equal to `tenant`.
    """

    company: str
    tenant: str
    dc: str
    site: str

    def __str__(self) -> str:
        """The identity slug, same role a bare string token plays for every
        other source. Lets ingest.py write company=str(token) and log
        f"{source}/{token}" without a Workday-specific branch — every token
        type answers "what's my company identity" via str()."""
        return self.company


@dataclass
class DetailResult:
    """fetch_detail()'s return type — no job_count, since a detail fetch is
    always exactly one posting, not a list."""

    status_code: int | None
    payload: Any | None
    error: str | None = None


def is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _error_from_response(resp: Any) -> str | None:
    """Workday's error responses (e.g. a bad-site 404) carry an errorCode
    and a human-readable message in the JSON body — surface both rather
    than just the bare status code, since that's what makes a bad `site` in
    companies.yaml distinguishable from an ordinary network failure."""
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    message = data.get("message")
    if message is None:
        return None
    error_code = data.get("errorCode")
    return f"{error_code}: {message}" if error_code else message


def _strip_video_info(value: Any) -> Any:
    """Recursively drop any "videoInfo" key, at any nesting depth — see
    module docstring for why. Returns a new structure; never mutates the
    input in place."""
    if isinstance(value, dict):
        return {k: _strip_video_info(v) for k, v in value.items() if k != "videoInfo"}
    if isinstance(value, list):
        return [_strip_video_info(v) for v in value]
    return value


def _request_with_retries(make_request: Any) -> tuple[int | None, dict | None, str | None]:
    """Shared retry/backoff loop for both the list POST and the detail GET.
    Returns (status_code, json_body, error); json_body is a dict only on a
    200 with valid JSON."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = make_request()
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            return None, None, str(exc)

        if resp.status_code == 200:
            try:
                return 200, resp.json(), None
            except ValueError as exc:
                return 200, None, f"bad json: {exc}"

        if is_transient(resp.status_code) and attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS[attempt]
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            if retry_after is not None:
                wait = retry_after
            time.sleep(wait)
            continue

        return resp.status_code, None, _error_from_response(resp)

    return None, None, "exhausted retries"


def _fetch_list_page(url: str, offset: int) -> tuple[int | None, dict | None, str | None]:
    body = {"appliedFacets": {}, "limit": PAGE_LIMIT, "offset": offset, "searchText": ""}
    headers = {**LIST_HEADERS, "User-Agent": USER_AGENT}
    return _request_with_retries(lambda: requests.post(url, json=body, headers=headers, timeout=TIMEOUT))


def fetch(token: WorkdayToken) -> FetchResult:
    url = LIST_URL_TEMPLATE.format(tenant=token.tenant, dc=token.dc, site=token.site)

    status, data, error = _fetch_list_page(url, offset=0)
    if status != 200 or data is None:
        return FetchResult(status_code=status, payload=None, job_count=0, error=error)

    postings = list(data.get("jobPostings", []))
    total = data.get("total", len(postings))
    pages_fetched = 1

    while len(postings) < total:
        if pages_fetched == MAX_PAGES:
            return FetchResult(
                status_code=200,
                payload=None,
                job_count=0,
                error=f"pagination cap of {MAX_PAGES} pages exceeded",
            )

        time.sleep(INTER_PAGE_SLEEP)
        status, data, error = _fetch_list_page(url, offset=len(postings))
        if status != 200 or data is None:
            return FetchResult(status_code=status, payload=None, job_count=0, error=error)

        page_postings = data.get("jobPostings", [])
        if not page_postings:
            # Workday silently returns an empty array instead of erroring
            # when there's nothing left at this offset (e.g. `total` was
            # stale, or a bug asked for more than PAGE_LIMIT) — treat it as
            # end of results rather than trusting `total` and looping.
            break

        postings.extend(page_postings)
        pages_fetched += 1

    return FetchResult(status_code=200, payload=postings, job_count=len(postings))


def fetch_detail(tenant: str, dc: str, site: str, external_path: str) -> DetailResult:
    url = DETAIL_URL_TEMPLATE.format(tenant=tenant, dc=dc, site=site, external_path=external_path)
    headers = {**DETAIL_HEADERS, "User-Agent": USER_AGENT}

    status, data, error = _request_with_retries(lambda: requests.get(url, headers=headers, timeout=TIMEOUT))
    if status != 200 or data is None:
        return DetailResult(status_code=status, payload=None, error=error)

    return DetailResult(status_code=200, payload=_strip_video_info(data), error=None)
