"""Detail-fetch layer for Workday postings: a separate pass, same shape as
parse_run.py and classify_run.py, run independently of ingest.py.

Workday's list endpoint (gradmarket.sources.workday.fetch) gives identity
and title only; description and location live behind a second, per-posting
GET (gradmarket.sources.workday.fetch_detail). This pass finds postings
from the most recent list payload per company that have no corresponding
raw_details row yet (db.get_postings_missing_detail) and fetches each one.

Retry/backoff for a single detail fetch lives entirely inside
gradmarket.sources.workday.fetch_detail() already — this module doesn't add
a second retry layer on top, it only paces between calls (INTER_REQUEST_SLEEP,
1s — see CLAUDE.md's ~1 req/sec-per-host constraint) and records whatever
fetch_detail() returns, success or failure alike, so a permanently-failing
posting (e.g. a stale externalPath) doesn't get retried indefinitely by a
future run — it simply gets a raw_details row with a null payload, which no
longer matches "no row yet".

Pacing is applied before every fetch after the first *attempted* one, not
"between all candidates" — a candidate skipped for having no companies.yaml
entry (see _load_workday_tokens) never makes a network call, so it costs no
sleep either.

--limit caps how many details are fetched in one run: a brand-new tenant's
backfill is its entire board, and fetching a several-thousand-posting board's
worth of details at 1/sec in one run would blow well past a daily cron
slot's budget.
"""

from __future__ import annotations

import argparse
import time

from gradmarket import db
from gradmarket.config import load_companies, resolve_companies_file
from gradmarket.sources import workday

SOURCE = "workday"
INTER_REQUEST_SLEEP = 1


def _load_workday_tokens() -> dict[str, workday.WorkdayToken]:
    """company -> WorkdayToken, from companies.yaml's `workday:` list.
    config.load_companies already turns each entry into a WorkdayToken (see
    CLAUDE.md's data model notes on Workday's three-part board identity);
    this just indexes them by company. Empty — not an error — when there's
    no `workday:` section yet."""
    companies = load_companies(resolve_companies_file())
    tokens = companies.get(SOURCE) or []
    return {token.company: token for token in tokens}


def run(*, limit: int | None = None) -> dict:
    conn = db.get_connection()
    db.init_schema(conn)

    tokens = _load_workday_tokens()
    candidates = db.get_postings_missing_detail(conn, source=SOURCE)

    attempted = 0
    succeeded = 0
    failed = 0
    skipped_unconfigured = 0

    for candidate in candidates:
        if limit is not None and attempted >= limit:
            break

        company = candidate["company"]
        token = tokens.get(company)
        if token is None:
            # In raw_fetches history but not (or no longer) in
            # companies.yaml — no tenant/dc/site to fetch against. Not
            # worth failing loudly every run; just skip it.
            skipped_unconfigured += 1
            continue

        if attempted > 0:
            time.sleep(INTER_REQUEST_SLEEP)

        result = workday.fetch_detail(token.tenant, token.dc, token.site, candidate["external_path"])
        attempted += 1

        db.insert_raw_detail(
            conn,
            source=SOURCE,
            company=company,
            external_id=candidate["external_id"],
            http_status=result.status_code,
            payload=result.payload,
        )

        if result.payload is not None:
            succeeded += 1
            print(f"{SOURCE}/{company}/{candidate['external_id']}: {result.status_code}")
        else:
            failed += 1
            detail = result.error or str(result.status_code)
            print(f"{SOURCE}/{company}/{candidate['external_id']}: FAILED ({detail})")

    conn.close()

    print()
    print("Summary:")
    print(f"  attempted: {attempted}")
    print(f"  succeeded: {succeeded}")
    print(f"  failed:    {failed}")
    if skipped_unconfigured:
        print(f"  skipped (no companies.yaml entry): {skipped_unconfigured}")

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped_unconfigured": skipped_unconfigured,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of detail fetches this run (a new tenant's backfill is its whole board)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(limit=args.limit)


if __name__ == "__main__":
    main()
