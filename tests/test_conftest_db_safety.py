"""Proves conftest.py's DATABASE_URL poisoning actually works: a real
connection attempt must fail fast and loud, not hang and not silently
succeed against production."""

from __future__ import annotations

import time

import psycopg
import pytest

from gradmarket import db


def test_real_db_connection_fails_fast_not_silently():
    start = time.monotonic()

    with pytest.raises(psycopg.OperationalError):
        db.get_connection()

    elapsed = time.monotonic() - start
    assert elapsed < 5, "connection attempt hung instead of failing fast"
