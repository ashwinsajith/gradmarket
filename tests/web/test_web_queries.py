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

from gradmarket.web.queries import (
    PUBLIC_COLUMNS,
    derive_role,
    fetch_arrival_counts,
    fetch_roles,
    fetch_stats,
    freshness_group,
)

DERIVED_FIELDS = {
    "days_since_first_seen",
    "days_open",
    "is_new",
    "is_long_running",
    "age_is_floor",
    "age_label",
    "freshness_group",
    "cities",
}

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


# --- freshness groups ---


def test_freshness_group_boundaries():
    assert [freshness_group(d, is_floor=False) for d in (0, 7, 8, 30, 31, 200)] == [
        "this_week",
        "this_week",
        "this_month",
        "this_month",
        "earlier",
        "earlier",
    ]


def test_a_floor_is_never_this_week():
    assert [freshness_group(d, is_floor=True) for d in (0, 3, 7, 8, 30, 31, 83)] == [
        "this_month",
        "this_month",
        "this_month",
        "this_month",
        "this_month",
        "earlier",
        "earlier",
    ]


def test_derive_role_sets_group_and_cities():
    role = derive_role(_row(first_seen_at=NOW - timedelta(days=10)), now=NOW)

    assert role.freshness_group == "this_month"
    assert role.cities == ("London",)


def test_floored_role_with_3_day_floor_is_this_month_not_this_week():
    started = NOW - timedelta(days=3)
    role = derive_role(_row(first_seen_at=started, board_started_at=started), now=NOW)

    assert role.age_is_floor
    assert role.freshness_group == "this_month"
    assert role.age_label == "3+ days" and not role.is_new


def test_unfloored_role_of_the_same_age_is_this_week():
    role = derive_role(_row(first_seen_at=NOW - timedelta(days=3)), now=NOW)

    assert not role.age_is_floor
    assert role.freshness_group == "this_week"


def test_arrival_counts_by_utc_day_include_closed_exclude_non_uk_and_non_early(pg_conn):
    # Observed since collection start, so nothing below is a floor.
    insert_raw_fetch(pg_conn, source="greenhouse", company="acme", fetched_at=COLLECTION_START)
    day = NOW - timedelta(days=5)
    insert_posting(pg_conn, external_id="a", first_seen_at=day)
    insert_posting(pg_conn, external_id="b", first_seen_at=day + timedelta(hours=1), is_open=False)
    insert_posting(pg_conn, external_id="c", first_seen_at=day, location_class="non_uk")
    insert_posting(pg_conn, external_id="d", first_seen_at=day, seniority_class="experienced")
    insert_posting(pg_conn, external_id="e", first_seen_at=NOW - timedelta(days=1))

    counts = fetch_arrival_counts(pg_conn)

    assert counts == {day.date(): 2, (NOW - timedelta(days=1)).date(): 1}


def test_board_added_last_week_counts_nothing_on_its_first_day_then_counts_arrivals(pg_conn):
    """Everything on a new board's first observed day was already live —
    not an arrival. Postings that appear from the next day on are."""
    first_day = NOW - timedelta(days=6)
    insert_raw_fetch(pg_conn, source="lever", company="newco", fetched_at=first_day)
    for i in range(5):
        insert_posting(pg_conn, external_id=f"live_{i}", source="lever", company="newco", first_seen_at=first_day)
    next_day = first_day + timedelta(days=1)
    insert_posting(pg_conn, external_id="arrived_1", source="lever", company="newco", first_seen_at=next_day)
    insert_posting(pg_conn, external_id="arrived_2", source="lever", company="newco",
                   first_seen_at=NOW - timedelta(days=2))

    counts = fetch_arrival_counts(pg_conn)

    assert first_day.date() not in counts
    assert counts[next_day.date()] == 1
    assert counts[(NOW - timedelta(days=2)).date()] == 1


def test_board_with_no_surviving_fetch_row_floors_its_first_postings(pg_conn):
    # raw_fetches pruned: the board's start comes from its earliest posting.
    insert_posting(pg_conn, external_id="old", source="ashby", company="oldco", first_seen_at=COLLECTION_START)
    insert_posting(pg_conn, external_id="new", source="ashby", company="oldco", first_seen_at=NOW - timedelta(days=1))

    counts = fetch_arrival_counts(pg_conn)

    assert COLLECTION_START.date() not in counts
    assert counts[(NOW - timedelta(days=1)).date()] == 1

