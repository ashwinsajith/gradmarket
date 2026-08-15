"""Runs the daily pipeline: ingest then parse, in one process.

Owns the healthcheck ping for both stages — ingest.py and parse_run.py stay
independently runnable without pinging anything themselves. This exists
because chaining them as separate commands (e.g. shell `&&`) lets ingest's
own ping report success before parse has even run. Pinging only after both
stages finish is the whole point of this module.

Fails (pings /fail, exits non-zero) if either stage raises, or if ingest
completed with zero boards succeeding. A parse failure after a successful
ingest still fails the run, but the log distinguishes the two: a collection
gap is urgent and unrecoverable for that day, a parser bug is recoverable
once fixed and the raw data it would have parsed is already safe in
raw_fetches.
"""

from __future__ import annotations

from gradmarket import health, ingest, parse_run


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

    health.ping_healthcheck(failed=not ingest_ok)


if __name__ == "__main__":
    main()
