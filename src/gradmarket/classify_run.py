"""Classification layer: tag postings with location_class and seniority_class.

A separate pass over postings, run independently of ingest.py and
parse_run.py — same shape as parse_run: idempotent by default (tracks
postings.classified_at), pass --full to reclassify every posting regardless
of whether it's already tagged. gradmarket.classify holds pure functions
with no DB access; this module owns all persistence.

Tags, never deletes: classification never touches is_open or closed_at, and
no posting is removed based on its classification.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from gradmarket import db
from gradmarket.classify.location import classify_location
from gradmarket.classify.seniority import classify_seniority


def classify_postings(postings: list[dict], *, classified_at: datetime) -> list[dict]:
    return [
        {
            "id": p["id"],
            "location_class": classify_location(p["location"]),
            "seniority_class": classify_seniority(p["title"], p["description_raw"]),
            "classified_at": classified_at,
        }
        for p in postings
    ]


def run(*, full: bool = False, dry_run: bool = False) -> dict:
    """dry_run runs all the same SQL — including schema setup, which always
    commits, since the columns have to exist for any of this to work — but
    the classification UPDATE stays uncommitted and gets rolled back at the
    end. Nothing is persisted; the returned counts describe what would have
    changed."""
    conn = db.get_connection()
    db.init_schema(conn)

    postings = db.get_postings_to_classify(conn, full=full)
    classified_at = datetime.now(UTC)
    classifications = classify_postings(postings, classified_at=classified_at)

    # One multi-row UPDATE ... FROM (VALUES ...) per CHUNK_SIZE postings, not
    # one UPDATE per posting.
    updated = db.bulk_update_classifications(conn, classifications=classifications, commit=not dry_run)

    location_counts: dict[str, int] = {}
    seniority_counts: dict[str, int] = {}
    for c in classifications:
        location_counts[c["location_class"]] = location_counts.get(c["location_class"], 0) + 1
        seniority_counts[c["seniority_class"]] = seniority_counts.get(c["seniority_class"], 0) + 1

    if dry_run:
        conn.rollback()
    conn.close()

    return {
        "processed": len(postings),
        "updated": updated,
        "dry_run": dry_run,
        "location_counts": location_counts,
        "seniority_counts": seniority_counts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Reclassify every posting, not just ones with classified_at IS NULL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the same SQL but roll back at the end; print what would change without persisting anything",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run(full=args.full, dry_run=args.dry_run)
    prefix = "[dry run] " if args.dry_run else ""
    print(f"{prefix}Classified {summary['processed']} posting(s), {summary['updated']} row(s) updated.")
    print(f"{prefix}location: {summary['location_counts']}")
    print(f"{prefix}seniority: {summary['seniority_counts']}")


if __name__ == "__main__":
    main()
