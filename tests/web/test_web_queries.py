from __future__ import annotations

import json
from datetime import datetime, timedelta

from web_seed import (
    COLLECTION_START,
    DESCRIPTION_SENTINEL,
    NOW,
    insert_posting,
    insert_raw_fetch,
    seed,
)

from gradmarket.web.queries import PUBLIC_COLUMNS, derive_role, fetch_roles, fetch_stats

DERIVED_FIELDS = {"days_since_first_seen", "days_open", "is_new", "is_long_running", "age_is_floor", "age_label"}

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


def test_query_returns_only_open_uk_early_postings(pg_conn):
    seed(pg_conn)

    roles = fetch_roles(pg_conn, now=NOW)

    assert [r.url.rsplit("/", 1)[1] for r in roles] == ["new", "newco_1", "mid", "floor"]


def test_query_derives_per_board_floors(pg_conn):
    seed(pg_conn)

    by_id = {r.url.rsplit("/", 1)[1]: r for r in fetch_roles(pg_conn, now=NOW)}

    assert by_id["floor"].age_label == "83+ days"
    assert by_id["newco_1"].age_is_floor and not by_id["newco_1"].is_new
    assert by_id["new"].is_new and not by_id["new"].age_is_floor
    assert by_id["mid"].age_label == "30 days"


def test_query_output_never_contains_description_text(pg_conn):
    seed(pg_conn)

    roles = fetch_roles(pg_conn, now=NOW)

    assert roles
    assert DESCRIPTION_SENTINEL not in json.dumps([r.to_json() for r in roles])


def test_stats_count_all_postings(pg_conn):
    seed(pg_conn)

    stats = fetch_stats(pg_conn)

    assert stats.postings_collected == 8
    assert stats.collecting_since == COLLECTION_START


def test_board_start_counts_closed_postings(pg_conn):
    """August postings all since closed, October ones open: the board started
    in August, so the October ages are exact, not floors."""
    insert_posting(pg_conn, external_id="aug_1", company="closedco", first_seen_at=COLLECTION_START, is_open=False)
    insert_posting(pg_conn, external_id="aug_2", company="closedco",
                   first_seen_at=COLLECTION_START + timedelta(days=4), is_open=False)
    insert_posting(pg_conn, external_id="oct_1", company="closedco", first_seen_at=NOW - timedelta(days=20))
    insert_posting(pg_conn, external_id="oct_2", company="closedco", first_seen_at=NOW - timedelta(days=5))

    roles = {r.url.rsplit("/", 1)[1]: r for r in fetch_roles(pg_conn, now=NOW)}

    assert set(roles) == {"oct_1", "oct_2"}
    assert not roles["oct_1"].age_is_floor and roles["oct_1"].age_label == "20 days"
    assert not roles["oct_2"].age_is_floor and roles["oct_2"].age_label == "5 days"
    assert roles["oct_2"].is_new


def test_empty_board_filling_up_shows_new_postings_not_floors(pg_conn):
    """A monitored board that returned 200 with no jobs until yesterday has
    no earlier postings at all — its surviving raw_fetches rows are what
    prove it was being watched, so its first postings are genuinely new."""
    insert_raw_fetch(pg_conn, source="ashby", company="quietco", fetched_at=NOW - timedelta(days=29))
    insert_posting(pg_conn, external_id="season_1", source="ashby", company="quietco",
                   first_seen_at=NOW - timedelta(days=1))

    (role,) = fetch_roles(pg_conn, now=NOW)

    assert not role.age_is_floor
    assert role.is_new
    assert role.age_label == "1 day"


def test_collecting_since_ignores_pruned_raw_fetches(pg_conn):
    """raw_fetches only holds the last ~30 days in production; the stat must
    still report the first day a posting was ever seen."""
    insert_raw_fetch(pg_conn, source="greenhouse", company="acme", fetched_at=NOW - timedelta(days=30))
    insert_posting(pg_conn, external_id="aug", first_seen_at=COLLECTION_START, is_open=False)
    insert_posting(pg_conn, external_id="oct", first_seen_at=NOW - timedelta(days=10))

    assert fetch_stats(pg_conn).collecting_since == COLLECTION_START
