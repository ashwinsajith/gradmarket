"""Runs the daily pipeline: ingest then parse then classify, in one process.

Owns the healthcheck ping for all three stages — ingest.py, parse_run.py,
and classify_run.py all stay independently runnable without pinging
anything themselves. This exists because chaining them as separate commands
(e.g. shell `&&`) lets an earlier stage's own ping report success before
later stages have even run. Pinging only after every stage finishes is the
whole point of this module.

Fails (pings /fail, exits non-zero) if any stage raises, or if ingest
completed with zero boards succeeding. Stopping at the first raised
exception mirrors shell `&&` semantics — later stages depend on data the
earlier ones would have written, so there's no value in still attempting
them. The log distinguishes failures by urgency: a collection gap (ingest)
is the most urgent and unrecoverable for that day; a parser bug is
recoverable once fixed, since the raw data it would have parsed is already
safe in raw_fetches; a classifier bug is the least urgent of the three —
classify/ is pure functions, re-run anytime, no data at risk at all.
"""

from __future__ import annotations

from gradmarket import classify_run, health, ingest, parse_run


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
        classify_summary = classify_run.run()
    except Exception as exc:
        print(
            f"PIPELINE FAILED: classify raised — least urgent of the three, "
            f"nothing lost, rerun anytime: {exc}"
        )
        health.ping_healthcheck(failed=True)
        raise

    print(f"classify ok — {classify_summary['processed']} posting(s) classified")

    health.ping_healthcheck(failed=not ingest_ok)


if __name__ == "__main__":
    main()
