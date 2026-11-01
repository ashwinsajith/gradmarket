"""Connection pool and in-memory snapshot cache for the web service.

The data changes once a day (pipeline.py), so Postgres is hit at most once
per CACHE_TTL, never per request. A refresh that fails keeps serving the
previous snapshot (logged) rather than taking the page down — /healthz
reports the snapshot's age, so a stuck refresh is visible there.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from psycopg_pool import ConnectionPool

from gradmarket.config import load_companies, resolve_companies_file
from gradmarket.web.queries import Role, Stats, fetch_roles, fetch_stats

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
RETRY_BACKOFF_SECONDS = 30

# Railway Postgres has dropped long-lived connections before: recycle idle
# connections after a minute and every connection after ten, and check each
# one before handing it out, so a dead socket is replaced rather than used.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 4
POOL_MAX_IDLE_SECONDS = 60
POOL_MAX_LIFETIME_SECONDS = 600
POOL_WAIT_TIMEOUT_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class Snapshot:
    roles: list[Role]
    stats: Stats
    boards_monitored: int


def _configure(conn: psycopg.Connection) -> None:
    # Every transaction on a pooled connection is BEGIN READ ONLY, so even a
    # bug here can't write.
    conn.read_only = True


def make_pool(conninfo: str) -> ConnectionPool:
    return ConnectionPool(
        conninfo,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        max_idle=POOL_MAX_IDLE_SECONDS,
        max_lifetime=POOL_MAX_LIFETIME_SECONDS,
        timeout=POOL_WAIT_TIMEOUT_SECONDS,
        kwargs={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
        configure=_configure,
        check=ConnectionPool.check_connection,
        open=False,
    )


def count_configured_boards() -> int:
    """'Boards monitored' means configured in companies.yaml today — not every
    (source, company) ever observed, which would still count boards since
    removed (e.g. Leonardo).

    Deliberately includes boards that have never produced a posting (~40
    return 200 with an empty feed): they're polled daily because an empty
    board filling up is how a hiring season opening gets detected. Don't
    filter them out, and don't add a "boards with postings" stat beside it."""
    return sum(len(tokens) for tokens in load_companies(resolve_companies_file()).values())


def load_snapshot(conn: psycopg.Connection, *, boards_monitored: int, now: datetime | None = None) -> Snapshot:
    return Snapshot(
        roles=fetch_roles(conn, now=now or datetime.now(UTC)),
        stats=fetch_stats(conn),
        boards_monitored=boards_monitored,
    )


class SnapshotCache:
    """Refresh-on-read: a request finding the snapshot older than `ttl`
    reloads it before answering. After a failed refresh, further attempts
    wait `retry_backoff` — otherwise every request during a database
    outage would sit through its own connect timeout before getting the
    same stale snapshot."""

    def __init__(
        self,
        loader: Callable[[], Snapshot],
        *,
        ttl: float = CACHE_TTL_SECONDS,
        retry_backoff: float = RETRY_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl = ttl
        self._retry_backoff = retry_backoff
        self._clock = clock
        self._snapshot: Snapshot | None = None
        self._loaded_at = 0.0
        self._next_attempt_at = 0.0
        self._lock = threading.Lock()

    def _is_fresh(self) -> bool:
        if self._snapshot is None:
            return False
        now = self._clock()
        return now - self._loaded_at < self._ttl or now < self._next_attempt_at

    def get(self) -> Snapshot:
        if self._is_fresh():
            return self._snapshot
        # One refresh at a time; requests arriving mid-refresh wait for it
        # and then see its result instead of each querying again.
        with self._lock:
            if self._is_fresh():
                return self._snapshot
            try:
                snapshot = self._loader()
            except Exception:
                if self._snapshot is None:
                    raise
                self._next_attempt_at = self._clock() + self._retry_backoff
                log.exception("snapshot refresh failed; serving snapshot from %.0fs ago", self.age())
                return self._snapshot
            self._snapshot = snapshot
            self._loaded_at = self._clock()
            return snapshot

    def age(self) -> float | None:
        """Seconds since the served snapshot was loaded, or None before the
        first successful load."""
        if self._snapshot is None:
            return None
        return self._clock() - self._loaded_at


def pooled_loader(pool: ConnectionPool) -> Callable[[], Snapshot]:
    boards_monitored = count_configured_boards()

    def load() -> Snapshot:
        with pool.connection() as conn:
            return load_snapshot(conn, boards_monitored=boards_monitored)

    return load
