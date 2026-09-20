"""Runs the daily pipeline: ingest, parse, detail fetch, reparse, then
classify, in one process.

Owns the healthcheck ping for all five stages — ingest.py, parse_run.py
(run twice), detail_run.py, and classify_run.py all stay independently
runnable without pinging anything themselves. This exists because chaining
them as separate commands (e.g. shell `&&`) lets an earlier stage's own
ping report success before later stages have even run. Pinging only after
every stage finishes is the whole point of this module.

Fails (pings /fail, exits non-zero) if any stage raises, or if ingest
completed with zero boards succeeding. Stopping at the first raised
exception mirrors shell `&&` semantics — later stages depend on data the
earlier ones would have written, so there's no value in still attempting
them. The log distinguishes failures by urgency: a collection gap (ingest)
is the most urgent and unrecoverable for that day; a parser bug is
recoverable once fixed, since the raw data it would have parsed is already
safe in raw_fetches — true of both parse passes equally; a detail-fetch
failure is even less urgent than that — no posting or raw list data is at
risk, only Workday's description/location enrichment for this cycle is
delayed; a classifier bug is the least urgent of the five — classify/ is
pure functions, re-run anytime, no data at risk at all.

Why parse runs twice, back to back with detail_run in between: a two-stage
source's postings (currently just Workday — see parse/base.py's
NEEDS_DETAILS) get created by the FIRST parse with location/description
still missing, since their detail hasn't been fetched yet at that point in
the cycle. detail_run then fetches what's missing and, for every company it
stored at least one new detail for, flags that company's most recent
raw_fetches row unparsed again (db.mark_latest_raw_fetch_unparsed). The
SECOND parse pass is what actually joins the newly-fetched details into
postings/posting_versions, by reparsing exactly those flagged rows — every
other row is already parsed and untouched, so this second pass is a no-op
whenever detail_run stored nothing new. Because this all happens before
classify_run runs, a brand-new Workday posting's very first classification
now sees its real description/location in the *same* cycle it was first
seen in, not a day or more later. (get_postings_to_classify's own
updated_at-based staleness check — see CLAUDE.md — is what makes even a
*later* cycle's join-in reliably trigger reclassification; this second
parse pass is what makes that not usually even necessary.)

DETAIL_RUN_LIMIT bounds detail_run per cycle for the same reason its own
--limit flag exists: a brand-new tenant's backfill is its entire board, and
fetching a several-thousand-posting board's worth of details at 1/sec would
blow well past this daily cron slot's budget. Ordinary day-to-day new-posting
volume is nowhere near this; it exists for the onboarding-a-large-tenant
case.
"""

from __future__ import annotations

from gradmarket import classify_run, detail_run, health, ingest, parse_run

DETAIL_RUN_LIMIT = 200


def main() -> None:
    try:
        _, succeeded = ingest.run()
    except Exception as exc:
        print(f"PIPELINE FAILED: ingest raised — collection gap, no new raw data collected: {exc}")
        health.ping_healthcheck(failed=True)
        raise

    ingest_ok = succeeded > 0
    if ingest_ok:
        print(f"ingest ok — {succeeded} board(s) succeeded")
    else:
        print("PIPELINE FAILED: ingest completed but zero boards succeeded — collection gap")

    try:
        parse_summary = parse_run.run()
    except Exception as exc:
        if ingest_ok:
            print(
                f"PIPELINE FAILED: parse raised after a successful ingest — "
                f"collection is safe, parsing needs a fix: {exc}"
            )
        else:
            print(f"PIPELINE FAILED: parse also raised, on top of zero boards succeeding: {exc}")
        health.ping_healthcheck(failed=True)
        raise

    print(f"parse ok — {parse_summary['processed']} row(s) processed")

    try:
        detail_summary = detail_run.run(limit=DETAIL_RUN_LIMIT)
    except Exception as exc:
        print(
            f"PIPELINE FAILED: detail fetch raised — no posting or raw data is at risk, "
            f"only Workday description/location enrichment is delayed: {exc}"
        )
        health.ping_healthcheck(failed=True)
        raise

    print(f"detail fetch ok — {detail_summary['attempted']} posting(s) attempted")

    try:
        reparse_summary = parse_run.run()
    except Exception as exc:
        print(
            f"PIPELINE FAILED: reparse (after detail fetch) raised — collection and the first "
            f"parse pass are safe, this second pass only joins fetched details in: {exc}"
        )
        health.ping_healthcheck(failed=True)
        raise

    print(f"reparse ok — {reparse_summary['processed']} row(s) processed")

    try:
        classify_summary = classify_run.run()
    except Exception as exc:
        print(
            f"PIPELINE FAILED: classify raised — least urgent of the five, "
            f"nothing lost, rerun anytime: {exc}"
        )
        health.ping_healthcheck(failed=True)
        raise

    print(f"classify ok — {classify_summary['processed']} posting(s) classified")

    health.ping_healthcheck(failed=not ingest_ok)


if __name__ == "__main__":
    main()
