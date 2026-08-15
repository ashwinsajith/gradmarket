"""Postgres connection and schema helpers.

Reads DATABASE_URL from the environment. Locally this points at Railway's
public endpoint; deployed services see the private one — same env var name,
different value per environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

# Postgres caps a single query at ~65535 parameters. bulk_upsert_postings uses
# 10 params/row (~6500 rows) and bulk_append_posting_versions uses 6 (~10900
# rows) before hitting that ceiling. No real company board is anywhere near
# CHUNK_SIZE rows today, but a bound that never triggers costs nothing, and
# the alternative is a latent crash the day one does.
CHUNK_SIZE = 1000


def _chunked(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fetches (
    id BIGSERIAL PRIMARY KEY,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    http_status INT,
    payload JSONB,
    parsed_at TIMESTAMPTZ
);
ALTER TABLE raw_fetches ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMPTZ;
DROP INDEX IF EXISTS raw_fetches_company_fetched_at_idx;
CREATE INDEX IF NOT EXISTS raw_fetches_source_company_fetched_at_idx
    ON raw_fetches (source, company, fetched_at);

CREATE TABLE IF NOT EXISTS postings (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT,
    location TEXT,
    department TEXT,
    url TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    is_open BOOLEAN NOT NULL DEFAULT true,
    closed_at TIMESTAMPTZ,
    content_hash TEXT NOT NULL,
    UNIQUE (source, company, external_id)
);
CREATE INDEX IF NOT EXISTS postings_source_company_idx ON postings (source, company);

CREATE TABLE IF NOT EXISTS posting_versions (
    id BIGSERIAL PRIMARY KEY,
    posting_id BIGINT NOT NULL REFERENCES postings(id),
    observed_at TIMESTAMPTZ NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT,
    location TEXT,
    description_raw TEXT
);
CREATE INDEX IF NOT EXISTS posting_versions_posting_id_idx ON posting_versions (posting_id);
"""


def get_connection() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])


def init_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def insert_raw_fetch(
    conn: psycopg.Connection,
    *,
    source: str,
    company: str,
    http_status: int | None,
    payload: Any | None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw_fetches (source, company, http_status, payload)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (source, company, http_status, Jsonb(payload) if payload is not None else None),
        )
        row_id = cur.fetchone()[0]
    conn.commit()
    return row_id


def get_unparsed_raw_fetches(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source, company, http_status, payload, fetched_at
            FROM raw_fetches
            WHERE parsed_at IS NULL
            ORDER BY fetched_at ASC
            """
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    return rows


def mark_raw_fetch_parsed(conn: psycopg.Connection, raw_fetch_id: int, *, commit: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE raw_fetches SET parsed_at = now() WHERE id = %s", (raw_fetch_id,))
    if commit:
        conn.commit()


def reset_parsed_state(conn: psycopg.Connection, *, commit: bool = True) -> None:
    """Wipe postings and posting_versions and mark every raw_fetches row unparsed.

    The derived tables are disposable and rebuildable from raw_fetches; this
    is what powers --full on parse_run. TRUNCATE is transactional in Postgres,
    so commit=False here correctly participates in a dry-run rollback too.
    """
    with conn.cursor() as cur:
        cur.execute("TRUNCATE posting_versions, postings")
        cur.execute("UPDATE raw_fetches SET parsed_at = NULL")
    if commit:
        conn.commit()


def get_previous_raw_payload(
    conn: psycopg.Connection, *, source: str, company: str, before: Any
) -> Any | None:
    """The payload of the most recent prior raw_fetches row for this company that had one."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload FROM raw_fetches
            WHERE source = %s AND company = %s AND fetched_at < %s AND payload IS NOT NULL
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (source, company, before),
        )
        row = cur.fetchone()
    return row[0] if row else None


def get_existing_posting_hashes(conn: psycopg.Connection, *, source: str, company: str) -> dict[str, str]:
    """{external_id: content_hash} for every posting on record for this company,
    open or closed. One query per company, not per posting."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT external_id, content_hash FROM postings WHERE source = %s AND company = %s",
            (source, company),
        )
        return dict(cur.fetchall())


def bulk_upsert_postings(
    conn: psycopg.Connection,
    *,
    source: str,
    company: str,
    postings: list[Any],
    observed_at: Any,
    commit: bool = True,
) -> dict[str, int]:
    """Upsert every posting for this company, chunked at CHUNK_SIZE rows per
    statement so one company's board can never hit Postgres's per-query
    parameter ceiling.

    Each item in `postings` must expose .external_id/.title/.location/
    .department/.url/.content_hash (a gradmarket.parse.base.ParsedPosting) —
    db.py doesn't import that type, just duck-types against it, to keep this
    module decoupled from the parse layer.

    first_seen_at is set only in the INSERT branch, never touched on update.
    closed_at is never touched here either — it's only ever written once, by
    close_missing_postings. Returns {external_id: posting_id}.
    """
    result: dict[str, int] = {}

    for chunk in _chunked(postings, CHUNK_SIZE):
        values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)"] * len(chunk))
        params: list[Any] = []
        for p in chunk:
            params.extend(
                [
                    source,
                    company,
                    p.external_id,
                    p.title,
                    p.location,
                    p.department,
                    p.url,
                    observed_at,
                    observed_at,
                    p.content_hash,
                ]
            )

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO postings (
                    source, company, external_id, title, location, department, url,
                    first_seen_at, last_seen_at, is_open, content_hash
                )
                VALUES {values_sql}
                ON CONFLICT (source, company, external_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    location = EXCLUDED.location,
                    department = EXCLUDED.department,
                    url = EXCLUDED.url,
                    last_seen_at = EXCLUDED.last_seen_at,
                    is_open = true,
                    content_hash = EXCLUDED.content_hash
                RETURNING external_id, id
                """,
                params,
            )
            result.update(cur.fetchall())
        if commit:
            conn.commit()

    return result


def bulk_append_posting_versions(
    conn: psycopg.Connection, *, versions: list[dict], commit: bool = True
) -> None:
    """Append every changed posting's new version, chunked at CHUNK_SIZE rows
    per statement for the same reason as bulk_upsert_postings.

    Each dict needs: posting_id, observed_at, content_hash, title, location,
    description_raw. Callers filter to only the postings whose hash changed —
    this function doesn't do that comparison itself.
    """
    for chunk in _chunked(versions, CHUNK_SIZE):
        values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(chunk))
        params: list[Any] = []
        for v in chunk:
            params.extend(
                [
                    v["posting_id"],
                    v["observed_at"],
                    v["content_hash"],
                    v["title"],
                    v["location"],
                    v["description_raw"],
                ]
            )

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO posting_versions (posting_id, observed_at, content_hash, title, location, description_raw)
                VALUES {values_sql}
                """,
                params,
            )
        if commit:
            conn.commit()


def close_missing_postings(
    conn: psycopg.Connection,
    *,
    source: str,
    company: str,
    seen_external_ids: set[str],
    closed_at: Any,
    commit: bool = True,
) -> int:
    """Close open postings for this company that weren't in this observation.

    closed_at IS NULL is part of the WHERE clause deliberately: a close date
    is written once, like first_seen_at. Without this guard, a posting that
    reopens and closes again later would have its original closure date
    overwritten by the later one.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE postings
            SET is_open = false, closed_at = %s
            WHERE source = %s AND company = %s
              AND is_open = true AND closed_at IS NULL
              AND NOT (external_id = ANY(%s))
            """,
            (closed_at, source, company, list(seen_external_ids)),
        )
        count = cur.rowcount
    if commit:
        conn.commit()
    return count


def get_open_source_companies(conn: psycopg.Connection) -> set[tuple[str, str]]:
    """Distinct (source, company) pairs with at least one open posting —
    reconciliation's candidate set to check against companies.yaml."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT source, company FROM postings WHERE is_open = true")
        return {(row[0], row[1]) for row in cur.fetchall()}


def close_orphaned_postings(
    conn: psycopg.Connection, *, source: str, company: str, commit: bool = True
) -> int:
    """Close every open posting for a company that no longer appears in
    companies.yaml at all — it stopped being fetched entirely, rather than
    being observed missing from a feed.

    closed_at is set to each row's own last_seen_at, not now: that's the last
    moment it was actually seen, and reconciliation happening today shouldn't
    imply it closed today. Same closed_at IS NULL write-once guard as
    close_missing_postings.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE postings
            SET is_open = false, closed_at = last_seen_at
            WHERE source = %s AND company = %s
              AND is_open = true AND closed_at IS NULL
            """,
            (source, company),
        )
        count = cur.rowcount
    if commit:
        conn.commit()
    return count
