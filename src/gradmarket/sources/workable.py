"""Workable job board source.

Implements the gradmarket.sources interface: fetch(token) -> FetchResult.

Verified against live calls before writing this: the real public endpoint is
apply.workable.com/api/v1/widget/accounts/{token} — the {token}.workable.com/
api/v3/... pattern people sometimes guess 404s everywhere it was tried.

Always requests ?details=true. Without it, jobs come back with no
description field at all (confirmed: same job, with and without the param,
gains a "description" key only when it's present) — and the seniority
classifier depends on description text, so an accidentally-bare fetch would
silently starve it.

Confirmed no pagination: ?page=2 and ?offset=100 both returned the exact same
full job list on a 145-job board. Response sizes across several real boards
(145, 125, 69, 7) don't clip at any round number either.

Observed a 429 with Retry-After: 82392 (~22.9h) — this is a daily quota, not
a short rate-limit window. Backoff can't outlast a day-long block, so a 429
carrying a Retry-After beyond MAX_RETRY_AFTER_SECONDS skips retrying entirely
rather than burning MAX_RETRIES attempts against a source that isn't coming
back within this run.
"""

from __future__ import annotations

import time

import requests

from gradmarket.sources.base import FetchResult

URL_TEMPLATE = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
TIMEOUT = 30
USER_AGENT = "gradmarket-ingest/0.1 (+https://github.com/ashwin-sajith/gradmarket)"
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]

# Workable rate-limits far more aggressively than Greenhouse, Lever or Ashby —
# discovery hit 429s even at 3s between requests. 1s (the other sources'
# pacing) stalls a daily ingest run in retries; 5s is the practical floor.
INTER_REQUEST_SLEEP = 5

# "A few minutes" — a 429's Retry-After beyond this is treated as a quota
# reset, not a short window worth backing off for (see module docstring).
MAX_RETRY_AFTER_SECONDS = 300


def is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))

            if resp.status_code == 429 and retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                return FetchResult(
                    status_code=429,
                    payload=None,
                    job_count=0,
                    error=(
                        f"rate limited, Retry-After={retry_after:.0f}s "
                        f"(~{retry_after / 3600:.1f}h) — not retrying, exceeds "
                        f"{MAX_RETRY_AFTER_SECONDS}s threshold"
                    ),
                )

            wait = retry_after if retry_after is not None else BACKOFF_SECONDS[attempt]
            time.sleep(wait)
            continue

        return FetchResult(status_code=resp.status_code, payload=None, job_count=0, error=None)

    return FetchResult(status_code=None, payload=None, job_count=0, error="exhausted retries")
