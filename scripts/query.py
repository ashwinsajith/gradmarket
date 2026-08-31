#!/usr/bin/env python3
"""Dev utility: run a SQL query and print the results.

Not part of the installed gradmarket package. Connects to DATABASE_URL via
gradmarket.db.get_connection() and runs the query in a read-only transaction
(the connection itself refuses writes), then prints an aligned table with
column headers. Run directly:

    python scripts/query.py "SELECT source, company, count(*) FROM postings GROUP BY 1, 2"
    python scripts/query.py path/to/query.sql
    python scripts/query.py "SELECT * FROM postings LIMIT 20" --csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from gradmarket import db


def load_sql(sql_or_path: str) -> str:
    path = Path(sql_or_path)
    if path.is_file():
        return path.read_text()
    return sql_or_path


def print_table(columns: list[str], rows: list[tuple]) -> None:
    str_rows = [["" if v is None else str(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    def format_row(values: list[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths, strict=True))

    print(format_row(columns))
    print("  ".join("-" * w for w in widths))
    for row in str_rows:
        print(format_row(row))


def print_csv(columns: list[str], rows: list[tuple]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(columns)
    writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sql", help="SQL to run, either inline or a path to a .sql file")
    parser.add_argument(
        "--csv", action="store_true", help="Print results as CSV instead of an aligned table"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sql = load_sql(args.sql)

    conn = db.get_connection()
    conn.read_only = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                print("query did not return rows", file=sys.stderr)
                return
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("(0 rows)", file=sys.stderr)

    if args.csv:
        print_csv(columns, rows)
    else:
        print_table(columns, rows)


if __name__ == "__main__":
    main()
