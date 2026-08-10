#!/usr/bin/env python3
"""Dev utility: sanity-check the raw_fetches schema against a real database.

Not part of the installed gradmarket package. Connects to DATABASE_URL, runs
init_schema, inserts a dummy row through the real insert path, reads it back,
then deletes it. Run directly:

    python scripts/smoke_db.py
"""

from __future__ import annotations

import sys

from gradmarket import db


def main() -> None:
    conn = db.get_connection()
    print("connected")

    db.init_schema(conn)
    print("init_schema ok")

    row_id = db.insert_raw_fetch(
        conn,
        source="smoke_test",
        company="smoke_test",
        http_status=200,
        payload={"smoke": True},
    )
    print(f"inserted row id={row_id}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT source, company, http_status, payload FROM raw_fetches WHERE id = %s",
            (row_id,),
        )
        row = cur.fetchone()

    if row is None:
        print(f"FAILED: could not read back row id={row_id}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    print(f"read back: {row}")

    with conn.cursor() as cur:
        cur.execute("DELETE FROM raw_fetches WHERE id = %s", (row_id,))
    conn.commit()
    print(f"deleted row id={row_id}")

    conn.close()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
