"""Shared seed rows for the web layer's real-Postgres tests (see conftest.py
in this directory). Every posting gets a posting_versions row, and every
raw_fetches row a payload, carrying DESCRIPTION_SENTINEL — so any query or
response that ever selected description text would surface it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
COLLECTION_START = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)

DESCRIPTION_SENTINEL = "SENTINEL-DESCRIPTION-TEXT-must-never-be-published"


def insert_raw_fetch(conn: psycopg.Connection, *, source: str, company: str, fetched_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_fetches (fetched_at, source, company, http_status, payload, parsed_at)"
            " VALUES (%s, %s, %s, 200, %s, %s)",
            (fetched_at, source, company, Jsonb({"jobs": [{"content": DESCRIPTION_SENTINEL}]}), fetched_at),
        )


def insert_posting(
    conn: psycopg.Connection,
    *,
    external_id: str,
    first_seen_at: datetime,
    source: str = "greenhouse",
    company: str = "acme",
    location_class: str | None = "uk",
    seniority_class: str | None = "early",
    is_open: bool = True,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO postings (source, company, external_id, title, location, department, url,
                first_seen_at, last_seen_at, is_open, content_hash, location_class, seniority_class)
            VALUES (%s, %s, %s, %s, 'London', 'Engineering', %s, %s, %s, %s, 'h', %s, %s)
            RETURNING id
            """,
            (
                source, company, external_id, f"Role {external_id}", f"https://example.test/{external_id}",
                first_seen_at, NOW - timedelta(hours=6), is_open, location_class, seniority_class,
            ),
        )
        posting_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO posting_versions (posting_id, observed_at, content_hash, title, location, description_raw)"
            " VALUES (%s, %s, 'h', 't', 'l', %s)",
            (posting_id, first_seen_at, DESCRIPTION_SENTINEL),
        )


def seed(conn: psycopg.Connection) -> None:
    # acme: observed since collection start (its raw row survives here; in
    # production it'd be pruned, and postings' own min carries the start).
    insert_raw_fetch(conn, source="greenhouse", company="acme", fetched_at=COLLECTION_START)
    insert_posting(conn, external_id="new", first_seen_at=NOW - timedelta(days=2))
    insert_posting(conn, external_id="mid", first_seen_at=NOW - timedelta(days=30))
    insert_posting(conn, external_id="floor", first_seen_at=COLLECTION_START)
    insert_posting(conn, external_id="closed", first_seen_at=NOW - timedelta(days=5), is_open=False)
    insert_posting(conn, external_id="non_uk", first_seen_at=NOW - timedelta(days=5), location_class="non_uk")
    insert_posting(conn, external_id="experienced", first_seen_at=NOW - timedelta(days=5), seniority_class="experienced")
    insert_posting(conn, external_id="unclassified", first_seen_at=NOW - timedelta(days=5),
                    location_class=None, seniority_class=None)
    # newco: board added 3 days ago, no surviving raw_fetches row at all.
    insert_posting(conn, external_id="newco_1", source="lever", company="newco",
                    first_seen_at=NOW - timedelta(days=3))
