#!/usr/bin/env python3
"""Dev utility: dry-run the parser against a real database and print what would change.

Not part of the installed gradmarket package. The dict-backed fake used in
tests/test_parse_run.py proves the parsing logic; it doesn't prove the SQL is
actually valid against Postgres. This does — it runs the real SQL against
DATABASE_URL inside a transaction that gets rolled back at the end, so
nothing is persisted. Run directly:

    python scripts/smoke_parse.py [--full]
"""

from __future__ import annotations

import sys

from gradmarket import db, parse_run


def main() -> None:
    full = "--full" in sys.argv[1:]

    summary = parse_run.run(full=full, dry_run=True)
    print(
        f"[dry run] would parse {summary['processed']} row(s), "
        f"skip {summary['skipped_failures']} failed fetch(es)"
    )
    print(
        f"[dry run] postings: {summary['postings_inserted']} would be inserted, "
        f"{summary['postings_updated']} would be updated, {summary['postings_closed']} would be closed"
    )
    print(f"[dry run] posting_versions: {summary['versions_appended']} would be appended")

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM postings")
        postings_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM postings WHERE is_open")
        open_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM posting_versions")
        versions_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM raw_fetches WHERE parsed_at IS NULL")
        unparsed_count = cur.fetchone()[0]
    conn.close()

    print(
        f"actual (unchanged) db state — postings: {postings_count} ({open_count} open), "
        f"posting_versions: {versions_count}, raw_fetches still unparsed: {unparsed_count}"
    )


if __name__ == "__main__":
    main()
