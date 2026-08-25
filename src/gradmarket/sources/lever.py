"""Lever job board source.

Implements the gradmarket.sources interface: fetch(token) -> FetchResult.

Lever returns a bare JSON array, paginated via skip/limit. Some accounts are
EU-hosted and 404 on the global host; those get one fallback attempt against
api.eu.lever.co before being recorded as a permanent failure. Pagination is
capped — a board that needs more than MAX_PAGES pages is more likely an API
bug than a real board, so it's recorded as a failure rather than returning
truncated data.

skip/limit pagination re-reads the list by numeric offset on every page, so
if the underlying list shifts between page requests (a job posted or removed
while paginating — plausible given INTER_PAGE_SLEEP between pages), an item
can cross the skip boundary and get read on two pages. fetch() deduplicates
by id across pages before returning, so raw_fetches never stores the same
job twice.
"""

from __future__ import annotations

import time

import requests

from gradmarket.sources.base import FetchResult

GLOBAL_HOST = "https://api.lever.co"
EU_HOST = "https://api.eu.lever.co"
PATH_TEMPLATE = "/v0/postings/{token}?mode=json&skip={skip}&limit={limit}"
PAGE_LIMIT = 100
MAX_PAGES = 50
TIMEOUT = 30
USER_AGENT = "gradmarket-ingest/0.1 (+https://github.com/ashwin-sajith/gradmarket)"
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]
INTER_PAGE_SLEEP = 1
INTER_REQUEST_SLEEP = 1  # between companies, read by ingest.py — distinct from INTER_PAGE_SLEEP above


def is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _fetch_page(host: str, token: str, skip: int) -> tuple[int | None, list | None, str | None]:
    """Return (status_code, page, error) for a single page. page is a list on success."""
    url = host + PATH_TEMPLATE.format(token=token, skip=skip, limit=PAGE_LIMIT)
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            return None, None, str(exc)

        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError as exc:
                return 200, None, f"bad json: {exc}"
            if not isinstance(data, list):
                return 200, None, f"unexpected payload shape: {type(data).__name__}"
            return 200, data, None

        if is_transient(resp.status_code) and attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS[attempt]
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    wait = float(retry_after)
                except ValueError:
                    pass
            time.sleep(wait)
            continue

        return resp.status_code, None, None

    return None, None, "exhausted retries"


def fetch(token: str) -> FetchResult:
    host = GLOBAL_HOST
    status, page, error = _fetch_page(host, token, skip=0)

    if status == 404:
        host = EU_HOST
        status, page, error = _fetch_page(host, token, skip=0)

    if status != 200 or page is None:
        return FetchResult(status_code=status, payload=None, job_count=0, error=error)

    all_jobs = list(page)
    pages_fetched = 1

    while len(page) == PAGE_LIMIT:
        if pages_fetched == MAX_PAGES:
            return FetchResult(
                status_code=200,
                payload=None,
                job_count=0,
                error=f"pagination cap of {MAX_PAGES} pages exceeded",
            )

        time.sleep(INTER_PAGE_SLEEP)
        status, page, error = _fetch_page(host, token, skip=len(all_jobs))
        if status != 200 or page is None:
            return FetchResult(status_code=status, payload=None, job_count=0, error=error)

        all_jobs.extend(page)
        pages_fetched += 1

    all_jobs = _deduplicate_jobs(all_jobs, token=token)

    return FetchResult(status_code=200, payload=all_jobs, job_count=len(all_jobs))


def _deduplicate_jobs(jobs: list[dict], *, token: str) -> list[dict]:
    """Keep the last occurrence of each id. Jobs without an id are left alone
    (not collapsed together) — extract() already warns and skips those
    individually; deduping them here by a shared None key would silently
    drop all but one."""
    with_id: dict[str, dict] = {}
    without_id: list[dict] = []
    for job in jobs:
        job_id = job.get("id")
        if job_id is None:
            without_id.append(job)
            continue
        if job_id in with_id:
            print(f"warning: lever/{token}: duplicate job id {job_id!r} across paginated pages, keeping last occurrence")
        with_id[job_id] = job
    return list(with_id.values()) + without_id
