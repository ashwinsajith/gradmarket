#!/usr/bin/env python3
"""Dev utility: probe Greenhouse board tokens for validity.

Not part of the installed gradmarket package. Run directly:

    python scripts/check_tokens.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
import yaml

DEFAULT_CANDIDATES_FILE = Path(__file__).resolve().parent / "candidates.txt"
URL_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
TIMEOUT = 10
USER_AGENT = "gradmarket-token-check/0.1 (+https://github.com/ashwin-sajith/gradmarket)"
INTER_REQUEST_SLEEP = 1
MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]


def read_tokens(path: Path) -> list[str]:
    tokens = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def fetch(token: str) -> tuple[str, int | None]:
    """Return (status_label, job_count). job_count is set only when status_label == '200'."""
    url = URL_TEMPLATE.format(token=token)
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            return f"error: {exc}", None

        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return "200 (bad json)", None
            return "200", len(data.get("jobs", []))

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

        return str(resp.status_code), None

    return "error: exhausted retries", None


def main() -> None:
    if len(sys.argv) > 1:
        candidates_file = Path(sys.argv[1])
    else:
        candidates_file = DEFAULT_CANDIDATES_FILE

    if not candidates_file.is_file():
        print(f"error: candidates file not found: {candidates_file}", file=sys.stderr)
        sys.exit(1)

    tokens = read_tokens(candidates_file)
    working: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for i, token in enumerate(tokens):
        status, count = fetch(token)
        if status == "200" and count is not None:
            working.append((token, count))
            print(f"{token}  {status}  {count} jobs")
        else:
            failed.append((token, status))
            print(f"{token}  {status}")

        if i < len(tokens) - 1:
            time.sleep(INTER_REQUEST_SLEEP)

    print("\n# Working tokens — paste into companies.yaml")
    print(yaml.dump([token for token, _ in working], default_flow_style=False, sort_keys=False))

    print("# Failed tokens")
    for token, status in failed:
        print(f"{token}: {status}")


if __name__ == "__main__":
    main()
