#!/usr/bin/env python3
"""Dev utility: re-resolve a labelled sample CSV's stale posting_id column.

Not part of the installed gradmarket package. A --full parse rebuild
TRUNCATEs postings and resets its id sequence, so posting_id values recorded
in an older labelled sample no longer match any row in the current table —
the labels themselves are still good (title/location/url didn't change),
only the id needs re-resolving. Looks each row's posting_id back up by url
against the current postings table and rewrites the file in place. Reports
any row whose url no longer resolves to anything, rather than guessing or
silently dropping it. Run directly:

    python scripts/rekey_labels.py [path]

path defaults to data/eval/sample.csv.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from gradmarket import db

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "sample.csv"
FIELDNAMES = [
    "posting_id",
    "source",
    "company",
    "title",
    "location",
    "url",
    "description_snippet",
    "label_location",
    "label_seniority",
]


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def fetch_url_to_id_map(conn, urls: list[str]) -> dict[str, str]:
    """One id per url. postings rows are never deleted (see CLAUDE.md), so a
    url should resolve unless it was never a real posting to begin with; if a
    url somehow matches more than one row, the most recently observed wins."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (url) url, id
            FROM postings
            WHERE url = ANY(%s)
            ORDER BY url, last_seen_at DESC
            """,
            (urls,),
        )
        return {url: str(post_id) for url, post_id in cur.fetchall()}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH

    if not path.is_file():
        print(f"error: {path} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(path)
    urls = [row["url"] for row in rows]

    conn = db.get_connection()
    url_to_id = fetch_url_to_id_map(conn, urls)
    conn.close()

    resolved = 0
    unresolved = []
    for row in rows:
        new_id = url_to_id.get(row["url"])
        if new_id is None:
            unresolved.append(row)
            continue
        row["posting_id"] = new_id
        resolved += 1

    save_rows(path, rows)

    print(f"{resolved} of {len(rows)} posting_id(s) re-resolved and rewritten to {path}")

    if unresolved:
        print(f"\n{len(unresolved)} row(s) did NOT resolve — url has no match in postings")
        print("(their posting_id was left unchanged, so it's still visibly stale):")
        for row in unresolved:
            print(f"  {row['company']} / {row['title']!r}: {row['url']}")
    else:
        print("all urls resolved.")


if __name__ == "__main__":
    main()
