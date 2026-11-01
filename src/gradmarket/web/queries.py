"""Read-only queries behind the public page, plus the derived age fields
that are the point of it.

The SQL names every column explicitly — never `SELECT *`, never a join to
posting_versions/raw_fetches/raw_details beyond the timestamps below —
so description text can't reach a response by accident.

Age floors are per board, not global. A board's postings that were already
live the first time we observed that board have an unknown real age: we
only know it's *at least* (now - first_seen_at). Collection start is one
such moment, but so is every board added since (157 boards were not all
configured on 10 August). A global "earliest fetch" would present a board
added last week as a set of brand-new postings, every one flagged "new".

A board's start is the earliest of min(raw_fetches.fetched_at) and
min(postings.first_seen_at) for it, the latter over ALL its postings,
closed ones included — open rows alone would give a board whose early
postings have all closed a late start, wrongly flooring ages we know
exactly. Neither term alone is enough: raw_fetches is pruned after
RAW_FETCHES_RETENTION_DAYS (so its min drifts forward to ~30 days ago for
every board), and postings only knows a board from its first *non-empty*
fetch — without the raw_fetches term, an empty board filling up (a hiring
season opening, the reason empty boards are monitored at all) would
present its first postings as floors instead of as new. Either term can
only move a start earlier, never later. When both are pruned/late the
error is conservative — a genuinely new posting reads as a floor
("N+ days"), never the reverse.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

NEW_WINDOW = timedelta(days=7)
FLOOR_WINDOW = timedelta(days=1)
LONG_RUNNING_DAYS = 60

# The only posting columns the public surface may expose (CLAUDE.md
# Constraints). Every one is either a listing-level fact or our own derived
# field — none is description text or derived from it.
PUBLIC_COLUMNS = (
    "title",
    "company",
    "location",
    "location_class",
    "source",
    "first_seen_at",
    "last_seen_at",
    "is_open",
    "url",
)

OPEN_UK_EARLY_ROLES_SQL = f"""
WITH board_start AS (
    SELECT source, company, min(observed_at) AS started_at
    FROM (
        SELECT source, company, min(fetched_at) AS observed_at
        FROM raw_fetches
        GROUP BY source, company
        UNION ALL
        SELECT source, company, min(first_seen_at)
        FROM postings
        GROUP BY source, company
    ) observations
    GROUP BY source, company
)
SELECT {", ".join(f"p.{c}" for c in PUBLIC_COLUMNS)}, b.started_at AS board_started_at
FROM postings p
JOIN board_start b ON b.source = p.source AND b.company = p.company
WHERE p.location_class = 'uk'
  AND p.seniority_class = 'early'
  AND p.is_open
ORDER BY p.first_seen_at DESC, p.id DESC
"""

# collecting_since is min(first_seen_at) over ALL postings, never
# min(raw_fetches.fetched_at): raw_fetches is pruned at 30 days, so its
# earliest row is about a month old, not the day collection started.
STATS_SQL = """
SELECT count(*) AS postings_collected, min(first_seen_at) AS collecting_since
FROM postings
"""


@dataclass(frozen=True)
class Role:
    title: str | None
    company: str
    location: str | None
    location_class: str
    source: str
    first_seen_at: datetime
    last_seen_at: datetime
    is_open: bool
    url: str | None
    days_since_first_seen: int
    days_open: int
    is_new: bool
    is_long_running: bool
    age_is_floor: bool
    age_label: str

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["first_seen_at"] = self.first_seen_at.isoformat()
        out["last_seen_at"] = self.last_seen_at.isoformat()
        return out


@dataclass(frozen=True)
class Stats:
    postings_collected: int
    collecting_since: datetime | None


def _age_label(days: int, *, is_floor: bool) -> str:
    if is_floor:
        return f"{days}+ days"
    return "1 day" if days == 1 else f"{days} days"


def derive_role(row: dict[str, Any], *, now: datetime) -> Role:
    """Pure: one query row -> one Role. No DB access, so the age rules are
    testable without Postgres.

    days_open equals days_since_first_seen for every row this page shows
    (the query only returns open postings); it's kept as its own field so a
    future closed-roles view can set it from closed_at without changing the
    shape.

    A floored posting is never "new" — first seen within 7 days only means
    its board was added within 7 days. "Long-running" stays valid for a
    floor, since the true age is at least the floor.
    """
    first_seen = row["first_seen_at"]
    days = (now - first_seen).days
    is_floor = first_seen < row["board_started_at"] + FLOOR_WINDOW

    return Role(
        **{c: row[c] for c in PUBLIC_COLUMNS},
        days_since_first_seen=days,
        days_open=days,
        is_new=not is_floor and first_seen >= now - NEW_WINDOW,
        is_long_running=days > LONG_RUNNING_DAYS,
        age_is_floor=is_floor,
        age_label=_age_label(days, is_floor=is_floor),
    )


def fetch_roles(conn: psycopg.Connection, *, now: datetime) -> list[Role]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(OPEN_UK_EARLY_ROLES_SQL)
        return [derive_role(row, now=now) for row in cur.fetchall()]


def fetch_stats(conn: psycopg.Connection) -> Stats:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(STATS_SQL)
        row = cur.fetchone()
    return Stats(postings_collected=row["postings_collected"], collecting_since=row["collecting_since"])
