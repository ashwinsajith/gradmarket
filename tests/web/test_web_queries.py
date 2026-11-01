from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from gradmarket.web.queries import PUBLIC_COLUMNS, derive_role, fetch_roles, fetch_stats

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
COLLECTION_START = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)

DERIVED_FIELDS = {"days_since_first_seen", "days_open", "is_new", "is_long_running", "age_is_floor", "age_label"}

DESCRIPTION_SENTINEL = "SENTINEL-DESCRIPTION-TEXT-must-never-be-published"


def _row(*, first_seen_at: datetime, board_started_at: datetime = COLLECTION_START) -> dict:
    return {
        "title": "Graduate Software Engineer",
        "company": "acme",
        "location": "London, UK",
        "location_class": "uk",
        "source": "greenhouse",
        "first_seen_at": first_seen_at,
        "last_seen_at": NOW - timedelta(hours=6),
        "is_open": True,
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "board_started_at": board_started_at,
    }


# --- derived fields (pure) ---


def test_recent_posting_is_new_with_exact_age():
    role = derive_role(_row(first_seen_at=NOW - timedelta(days=2, hours=3)), now=NOW)

    assert role.days_since_first_seen == 2
    assert role.days_open == 2
    assert role.is_new
    assert not role.age_is_floor
    assert not role.is_long_running
    assert role.age_label == "2 days"


def test_new_window_is_seven_days_inclusive():
    assert derive_role(_row(first_seen_at=NOW - timedelta(days=7)), now=NOW).is_new
    assert not derive_role(_row(first_seen_at=NOW - timedelta(days=7, seconds=1)), now=NOW).is_new


def test_posting_live_at_collection_start_is_a_floor_not_an_exact_age():
    role = derive_role(_row(first_seen_at=COLLECTION_START), now=NOW)

    assert role.age_is_floor
    assert role.days_since_first_seen == 83
    assert role.age_label == "83+ days"
    assert role.is_long_running  # true age >= floor > 60, so this one is certain


def test_floor_window_is_one_day_after_board_start():
    just_inside = derive_role(_row(first_seen_at=COLLECTION_START + timedelta(hours=23)), now=NOW)
    at_edge = derive_role(_row(first_seen_at=COLLECTION_START + timedelta(days=1)), now=NOW)

    assert just_inside.age_is_floor
    assert not at_edge.age_is_floor
    assert at_edge.age_label == "82 days"


def test_board_added_recently_is_a_floor_and_never_new():
    """A board configured 3 days ago: everything on it was first seen 3 days
    ago, but none of it is known to be new."""
    started = NOW - timedelta(days=3)
    role = derive_role(_row(first_seen_at=started, board_started_at=started), now=NOW)

    assert role.age_is_floor
    assert not role.is_new
    assert not role.is_long_running
    assert role.age_label == "3+ days"


def test_long_running_threshold_is_more_than_sixty_days():
    assert not derive_role(_row(first_seen_at=NOW - timedelta(days=60, hours=1)), now=NOW).is_long_running
    assert derive_role(_row(first_seen_at=NOW - timedelta(days=61)), now=NOW).is_long_running


def test_singular_day_label():
    assert derive_role(_row(first_seen_at=NOW - timedelta(days=1, hours=1)), now=NOW).age_label == "1 day"


def test_json_shape_is_public_columns_plus_derived_only():
    out = derive_role(_row(first_seen_at=NOW - timedelta(days=2)), now=NOW).to_json()

    assert set(out) == set(PUBLIC_COLUMNS) | DERIVED_FIELDS
    assert "board_started_at" not in out
    json.dumps(out)  # serialisable as-is


# --- SQL (real Postgres, rolled back; see tests/web/conftest.py) ---


def _insert_raw_fetch(conn: psycopg.Connection, *, source: str, company: str, fetched_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_fetches (fetched_at, source, company, http_status, payload, parsed_at)"
            " VALUES (%s, %s, %s, 200, %s, %s)",
            (fetched_at, source, company, Jsonb({"jobs": [{"content": DESCRIPTION_SENTINEL}]}), fetched_at),
        )


def _insert_posting(
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


def _seed(conn: psycopg.Connection) -> None:
    # acme: observed since collection start (its raw row survives here; in
    # production it'd be pruned, and postings' own min carries the start).
    _insert_raw_fetch(conn, source="greenhouse", company="acme", fetched_at=COLLECTION_START)
    _insert_posting(conn, external_id="new", first_seen_at=NOW - timedelta(days=2))
    _insert_posting(conn, external_id="mid", first_seen_at=NOW - timedelta(days=30))
    _insert_posting(conn, external_id="floor", first_seen_at=COLLECTION_START)
    _insert_posting(conn, external_id="closed", first_seen_at=NOW - timedelta(days=5), is_open=False)
    _insert_posting(conn, external_id="non_uk", first_seen_at=NOW - timedelta(days=5), location_class="non_uk")
    _insert_posting(conn, external_id="experienced", first_seen_at=NOW - timedelta(days=5), seniority_class="experienced")
    _insert_posting(conn, external_id="unclassified", first_seen_at=NOW - timedelta(days=5),
                    location_class=None, seniority_class=None)
    # newco: board added 3 days ago, no surviving raw_fetches row at all.
    _insert_posting(conn, external_id="newco_1", source="lever", company="newco",
                    first_seen_at=NOW - timedelta(days=3))


def test_query_returns_only_open_uk_early_postings(pg_conn):
    _seed(pg_conn)

    roles = fetch_roles(pg_conn, now=NOW)

    assert [r.url.rsplit("/", 1)[1] for r in roles] == ["new", "newco_1", "mid", "floor"]


def test_query_derives_per_board_floors(pg_conn):
    _seed(pg_conn)

    by_id = {r.url.rsplit("/", 1)[1]: r for r in fetch_roles(pg_conn, now=NOW)}

    assert by_id["floor"].age_label == "83+ days"
    assert by_id["newco_1"].age_is_floor and not by_id["newco_1"].is_new
    assert by_id["new"].is_new and not by_id["new"].age_is_floor
    assert by_id["mid"].age_label == "30 days"


def test_query_output_never_contains_description_text(pg_conn):
    _seed(pg_conn)

    roles = fetch_roles(pg_conn, now=NOW)

    assert roles
    assert DESCRIPTION_SENTINEL not in json.dumps([r.to_json() for r in roles])


def test_stats_count_all_postings(pg_conn):
    _seed(pg_conn)

    stats = fetch_stats(pg_conn)

    assert stats.postings_collected == 8
    assert stats.collecting_since == COLLECTION_START
