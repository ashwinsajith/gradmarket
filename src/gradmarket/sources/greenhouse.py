"""Greenhouse job board source.

Implements the gradmarket.sources interface: fetch(token) -> FetchResult.
Rate-limit pacing between calls is the caller's responsibility (see
ingest.py), not this module's.
"""

from __future__ import annotations

import time

import requests

from gradmarket.sources.base import FetchResult

URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
TIMEOUT = 30
USER_AGENT = "gradmarket-ingest/0.1 (+https://github.com/ashwin-sajith/gradmarket)"
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]


def is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def fetch(token: str) -> FetchResult:
    url = URL_TEMPLATE.format(token=token)
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            return FetchResult(status_code=None, payload=None, job_count=0, error=str(exc))

        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError as exc:
                return FetchResult(status_code=200, payload=None, job_count=0, error=f"bad json: {exc}")
            jobs = data.get("jobs", [])
            return FetchResult(status_code=200, payload=data, job_count=len(jobs))

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

        return FetchResult(status_code=resp.status_code, payload=None, job_count=0, error=None)

    return FetchResult(status_code=None, payload=None, job_count=0, error="exhausted retries")
