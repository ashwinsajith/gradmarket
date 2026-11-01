from __future__ import annotations

from datetime import date, timedelta

from gradmarket.web.arrivals import RECENT_DAYS, build_arrivals

START = date(2026, 8, 10)
TODAY = date(2026, 11, 1)


def _by_day(arrivals):
    return {d.day: d for d in arrivals.days}


def test_one_bar_per_day_from_start_to_today_inclusive_zero_filled():
    arrivals = build_arrivals({date(2026, 9, 1): 4}, start=START, today=TODAY)

    assert arrivals.days[0].day == START
    assert arrivals.days[-1].day == TODAY
    assert len(arrivals.days) == (TODAY - START).days + 1
    assert _by_day(arrivals)[date(2026, 9, 1)].count == 4
    assert _by_day(arrivals)[date(2026, 9, 2)].count == 0


def test_scale_and_peak_come_from_the_largest_day():
    counts = {date(2026, 9, 3): 20, date(2026, 10, 1): 10}

    arrivals = build_arrivals(counts, start=START, today=TODAY)
    days = _by_day(arrivals)

    assert arrivals.peak.day == date(2026, 9, 3)
    assert arrivals.peak.count == 20
    assert days[date(2026, 9, 3)].height_pct == 100.0
    assert days[date(2026, 10, 1)].height_pct == 50.0


def test_peak_tie_goes_to_the_earliest_day():
    arrivals = build_arrivals({date(2026, 9, 3): 9, date(2026, 10, 3): 9}, start=START, today=TODAY)

    assert arrivals.peak.day == date(2026, 9, 3)


def test_no_peak_when_nothing_arrived():
    arrivals = build_arrivals({}, start=START, today=TODAY)

    assert arrivals.peak is None
    assert all(d.height_pct == 0.0 for d in arrivals.days)


def test_most_recent_seven_days_are_recent():
    arrivals = build_arrivals({}, start=START, today=TODAY)

    recent = [d.day for d in arrivals.days if d.is_recent]
    assert recent == [TODAY - timedelta(days=i) for i in reversed(range(RECENT_DAYS))]


def test_month_spans_cover_every_day_with_initials():
    arrivals = build_arrivals({}, start=START, today=TODAY)

    assert [(m.initial, m.name, m.days) for m in arrivals.months] == [
        ("A", "August", 22),
        ("S", "September", 30),
        ("O", "October", 31),
        ("N", "November", 1),
    ]
    assert sum(m.days for m in arrivals.months) == len(arrivals.days)


def test_empty_without_a_start_or_with_a_future_start():
    assert build_arrivals({}, start=None, today=TODAY).days == ()
    assert build_arrivals({}, start=TODAY + timedelta(days=1), today=TODAY).days == ()


def test_json_shape():
    out = build_arrivals({date(2026, 9, 3): 5}, start=START, today=TODAY).to_json()

    assert set(out) == {"start", "end", "peak", "days"}
    assert out["start"] == "2026-08-10" and out["end"] == "2026-11-01"
    assert out["peak"] == {"date": "2026-09-03", "count": 5}
    assert out["days"][0] == {"date": "2026-08-10", "count": 0}
