"""Day-one collection layer: fetch every configured board, archive raw responses.

No parsing, no postings table — that's a separate pass over raw_fetches.
Contains no provider-specific logic; dispatches through gradmarket.sources.SOURCES.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests
import yaml

from gradmarket import db
from gradmarket.sources import SOURCES

INTER_REQUEST_SLEEP = 1
HEALTHCHECK_TIMEOUT = 10


def ping_healthcheck(*, failed: bool) -> None:
    base_url = os.environ.get("HEALTHCHECK_URL")
    if not base_url:
        return
    url = f"{base_url}/fail" if failed else base_url
    try:
        requests.get(url, timeout=HEALTHCHECK_TIMEOUT)
    except requests.RequestException as exc:
        print(f"healthcheck ping failed: {exc}")


def resolve_companies_file() -> Path:
    env_value = os.environ.get("COMPANIES_FILE")
    path = Path(env_value) if env_value else Path.cwd() / "companies.yaml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"companies file not found: {path}")
    return path


def load_companies(path: Path) -> dict[str, list[str]]:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _run() -> tuple[int, int]:
    """Run the ingest, returning (attempted, succeeded)."""
    companies = load_companies(resolve_companies_file())
    jobs_to_fetch = [
        (source_name, token) for source_name, tokens in companies.items() for token in tokens
    ]

    conn = db.get_connection()
    db.init_schema(conn)

    attempted = 0
    succeeded = 0
    failed = 0
    total_jobs = 0

    for i, (source_name, token) in enumerate(jobs_to_fetch):
        result = SOURCES[source_name].fetch(token)
        attempted += 1

        db.insert_raw_fetch(
            conn,
            source=source_name,
            company=token,
            http_status=result.status_code,
            payload=result.payload,
        )

        if result.payload is not None:
            succeeded += 1
            total_jobs += result.job_count
            print(f"{source_name}/{token}: {result.status_code} ({result.job_count} jobs)")
        else:
            failed += 1
            detail = result.error or str(result.status_code)
            print(f"{source_name}/{token}: FAILED ({detail})")

        if i < len(jobs_to_fetch) - 1:
            time.sleep(INTER_REQUEST_SLEEP)

    conn.close()

    print()
    print("Summary:")
    print(f"  attempted: {attempted}")
    print(f"  succeeded: {succeeded}")
    print(f"  failed:    {failed}")
    print(f"  total jobs seen: {total_jobs}")

    return attempted, succeeded


def main() -> None:
    try:
        _, succeeded = _run()
    except Exception:
        ping_healthcheck(failed=True)
        raise

    ping_healthcheck(failed=succeeded == 0)


if __name__ == "__main__":
    main()
