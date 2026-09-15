"""Runs the daily pipeline: ingest, parse, detail fetch, then classify, in
one process.

Owns the healthcheck ping for all four stages — ingest.py, parse_run.py,
detail_run.py, and classify_run.py all stay independently runnable without
pinging anything themselves. This exists because chaining them as separate
commands (e.g. shell `&&`) lets an earlier stage's own ping report success
before later stages have even run. Pinging only after every stage finishes
is the whole point of this module.

Fails (pings /fail, exits non-zero) if any stage raises, or if ingest
completed with zero boards succeeding. Stopping at the first raised
exception mirrors shell `&&` semantics — later stages depend on data the
earlier ones would have written, so there's no value in still attempting
them. The log distinguishes failures by urgency: a collection gap (ingest)
is the most urgent and unrecoverable for that day; a parser bug is
recoverable once fixed, since the raw data it would have parsed is already
safe in raw_fetches; a detail-fetch failure is even less urgent than
that — no posting or raw list data is at risk, only Workday's
description/location enrichment for this cycle is delayed; a classifier bug
is the least urgent of the four — classify/ is pure functions, re-run
anytime, no data at risk at all.

detail_run runs after parse and before classify so a brand-new Workday
posting gets a chance at its description before classification sees it —
but this is best-effort within a single cycle, not a guarantee. parse_run
already ran by the time detail_run fetches a new posting's detail this same
cycle, so that freshly-fetched detail doesn't reach postings/
posting_versions until a LATER day's parse pass re-reads a fresh raw_fetches
row for it (see parse/workday.py's module docstring on the two-stage raw
tables). Concretely: a new Workday posting's very first classification can
happen with location=None/description_raw=None, and since classify_run only
classifies a posting once (classified_at IS NULL), that first, incomplete
classification is never automatically redone once the real description
lands a day or two later.

Decision: this is accepted, not fixed, for now. Workday's footprint is a
single company today, so the blast radius is small, and unlike a collection
gap this is trivially correctable — classify_run --full re-tags every
posting, pure functions, no data at risk (see the severity note above and
CLAUDE.md). Building machinery to detect "classified before its first real
description arrived" and selectively reclassify just those postings would
be real, non-trivial scope for a problem that a periodic --full already
fixes for free. Revisit if Workday's postings volume grows enough that a
day-one misclassification rate actually matters for search quality.

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
        classify_summary = classify_run.run()
    except Exception as exc:
        print(
            f"PIPELINE FAILED: classify raised — least urgent of the four, "
            f"nothing lost, rerun anytime: {exc}"
        )
        health.ping_healthcheck(failed=True)
        raise

    print(f"classify ok — {classify_summary['processed']} posting(s) classified")

    health.ping_healthcheck(failed=not ingest_ok)


if __name__ == "__main__":
    main()
