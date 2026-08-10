"""Postgres connection and schema helpers.

Reads DATABASE_URL from the environment. Locally this points at Railway's
public endpoint; deployed services see the private one — same env var name,
different value per environment.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fetches (
    id BIGSERIAL PRIMARY KEY,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    http_status INT,
    payload JSONB
);
CREATE INDEX IF NOT EXISTS raw_fetches_company_fetched_at_idx
    ON raw_fetches (company, fetched_at);
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
