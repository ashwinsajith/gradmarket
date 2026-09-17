#!/usr/bin/env python3
"""Discovery utility: probe Workday boards for facets that identify early-careers postings.

Not part of the installed gradmarket package. For each candidate board,
requests the job list (limit=1 — only the facet metadata in the response is
wanted, not the postings) and reports any facet value whose descriptor looks
like an early-careers category (graduate, intern, apprentice, campus,
placement, trainee, student, entry). This is how Barclays' workerSubType
Graduate/Intern/Apprentice ids were found (see CLAUDE.md) — the ids this
prints are what go into companies.yaml's `facets:` field for
gradmarket.sources.workday.fetch() to send as appliedFacets. Facet ids are
opaque and tenant-specific: never carry one over from a different board, and
never guess one from its descriptor alone — always confirm it here first.

Uses requests, not urllib.request — urllib fails Workday's TLS certificate
verification on macOS (it doesn't use the OS trust store the way requests,
via certifi, does).

Usage:
    python scripts/probe_facets.py [candidates_file]
    (default: scripts/candidates_workday.txt — same company,tenant,dc,site
    format check_tokens.py --source workday reads; read_workday_candidates
    is reused from there rather than re-parsed here.)
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_tokens import DEFAULT_WORKDAY_CANDIDATES_FILE, read_workday_candidates

from gradmarket.sources.workday import (
    LIST_HEADERS,
    LIST_URL_TEMPLATE,
    TIMEOUT,
    USER_AGENT,
    WorkdayToken,
)

EARLY_CAREERS_DESCRIPTOR_PATTERN = re.compile(
    r"graduate|intern|apprentice|campus|placement|trainee|student|entry",
    re.IGNORECASE,
)

INTER_REQUEST_SLEEP = 1


def probe(token: WorkdayToken) -> None:
    url = LIST_URL_TEMPLATE.format(tenant=token.tenant, dc=token.dc, site=token.site)
    headers = {**LIST_HEADERS, "User-Agent": USER_AGENT}
    body = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"{token.company}: FAILED ({exc})")
        return

    if resp.status_code != 200:
        print(f"{token.company}: FAILED (status {resp.status_code})")
        return

    try:
        data = resp.json()
    except ValueError as exc:
        print(f"{token.company}: FAILED (bad json: {exc})")
        return

    total = data.get("total", "?")
    hits = [
        (facet.get("facetParameter"), value.get("descriptor") or "", value.get("id"), value.get("count"))
        for facet in data.get("facets", [])
        for value in facet.get("values", [])
        if EARLY_CAREERS_DESCRIPTOR_PATTERN.search(value.get("descriptor") or "")
    ]

    print(f"\n{token.company}  (total {total})")
    if not hits:
        print("   no early-careers facet values found")
        return
    for param, desc, facet_id, count in hits:
        print(f"   {param:22} {desc:34} {count:>5}  {facet_id}")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKDAY_CANDIDATES_FILE
    if not path.is_file():
        print(f"error: candidates file not found: {path}", file=sys.stderr)
        sys.exit(1)

    tokens = read_workday_candidates(path)
    for i, token in enumerate(tokens):
        probe(token)
        if i < len(tokens) - 1:
            time.sleep(INTER_REQUEST_SLEEP)


if __name__ == "__main__":
    main()
