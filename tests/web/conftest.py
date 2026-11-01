"""Real-Postgres fixture for the web layer's SQL, inside a rolled-back
transaction.

Everything else in this suite uses FakeDBs, but "closed / non-UK /
non-early rows are excluded" and "no description column is selected" are
claims about SQL, which only a real database can check.

Opt-in: these tests skip unless GRADMARKET_TEST_DATABASE_URL is set. It is
deliberately not DATABASE_URL (conftest.py poisons that for the whole
session), and it must name a database with "test" in its name — so a
pasted production URL is refused rather than written to, even inside a
transaction that's rolled back. Throwaway local database:

    docker run --rm -d --name gradmarket-test-pg -p 54329:5432 \\
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=gradmarket_test postgres:18
    export GRADMARKET_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:54329/gradmarket_test
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from gradmarket import db

TEST_DB_ENV = "GRADMARKET_TEST_DATABASE_URL"


@pytest.fixture
def pg_conn() -> Iterator[psycopg.Connection]:
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set")
    if "test" not in (conninfo_to_dict(url).get("dbname") or ""):
        pytest.fail(f"{TEST_DB_ENV} must name a database containing 'test'; refusing to use it")

    conn = psycopg.connect(url, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(db.SCHEMA)  # DDL is transactional in Postgres; rolled back too
        yield conn
    finally:
        conn.rollback()
        conn.close()
