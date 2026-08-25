"""Day-one collection layer: fetch every configured board, archive raw responses.

No parsing, no postings table — that's a separate pass over raw_fetches.
Contains no provider-specific logic; dispatches through gradmarket.sources.SOURCES.
Independently runnable — pings nothing itself; see pipeline.py for that.

Inter-request pacing is per-source: each source module declares its own
INTER_REQUEST_SLEEP (part of the source interface, alongside fetch()), since
some — Workable — rate-limit far more aggressively than others. If a source
still returns CIRCUIT_BREAKER_THRESHOLD consecutive 429s despite that pacing
(each of those fetch() calls already exhausted its own internal retry/backoff
before returning 429 — see MAX_RETRIES/BACKOFF_SECONDS in the source module),
that source is treated as unavailable for the rest of this run: its remaining
tokens are recorded as failed without attempting them, rather than each one
burning through a full retry cycle against a source that isn't going to
answer. A collection gap on one source this way doesn't stall or block the
other three.
"""

from __future__ import annotations

import time

from gradmarket import db
from gradmarket.config import load_companies, resolve_companies_file
from gradmarket.sources import SOURCES

CIRCUIT_BREAKER_THRESHOLD = 3


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
    consecutive_429s: dict[str, int] = {}
    tripped: set[str] = set()

    for i, (source_name, token) in enumerate(jobs_to_fetch):
        attempted += 1

        if source_name in tripped:
            db.insert_raw_fetch(conn, source=source_name, company=token, http_status=None, payload=None)
            failed += 1
            print(f"{source_name}/{token}: SKIPPED ({source_name} circuit breaker tripped this run)")
            continue

        result = SOURCES[source_name].fetch(token)

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
            consecutive_429s[source_name] = 0
            print(f"{source_name}/{token}: {result.status_code} ({result.job_count} jobs)")
        else:
            failed += 1
            detail = result.error or str(result.status_code)
            print(f"{source_name}/{token}: FAILED ({detail})")

            if result.status_code == 429:
                consecutive_429s[source_name] = consecutive_429s.get(source_name, 0) + 1
                if consecutive_429s[source_name] >= CIRCUIT_BREAKER_THRESHOLD:
                    tripped.add(source_name)
                    print(
                        f"{source_name}: {CIRCUIT_BREAKER_THRESHOLD} consecutive 429s, "
                        f"each already retried internally — skipping remaining "
                        f"{source_name} tokens for this run"
                    )
            else:
                consecutive_429s[source_name] = 0

        if i < len(jobs_to_fetch) - 1 and source_name not in tripped:
            time.sleep(SOURCES[source_name].INTER_REQUEST_SLEEP)

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
