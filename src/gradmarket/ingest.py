"""Day-one collection layer: fetch every configured board, archive raw responses.

No parsing, no postings table — that's a separate pass over raw_fetches.
Contains no provider-specific logic; dispatches through gradmarket.sources.SOURCES.
Independently runnable — pings nothing itself; see pipeline.py for that.
"""

from __future__ import annotations

import time

from gradmarket import db
from gradmarket.config import load_companies, resolve_companies_file
from gradmarket.sources import SOURCES

INTER_REQUEST_SLEEP = 1


def run() -> tuple[int, int]:
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
    run()


if __name__ == "__main__":
    main()
