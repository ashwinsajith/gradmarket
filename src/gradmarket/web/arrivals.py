"""Daily arrivals: how many UK early-careers postings genuinely appeared
each day. Pure — the counts come from queries.fetch_arrival_counts at
snapshot refresh, which already leaves out floors (postings that were
live before their board was first observed); everything here is arithmetic
over those counts.

Because floors are excluded at the source, the days the board list grew
(collection's first day included) need no special handling: the postings
that would have spiked them simply aren't arrivals. The chart's early days
are near-zero, which is the honest answer — almost everything live then
was already live before we looked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

RECENT_DAYS = 7
# A final month this short gets its label right-aligned, so "NOV" on 1 Nov
# reads leftwards from the chart's edge instead of running off it.
SHORT_TRAILING_MONTH_DAYS = 7

# Fixed, not strftime("%b"): month names must not depend on the server's
# locale.
MONTH_ABBRS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@dataclass(frozen=True)
class ArrivalDay:
    day: date
    count: int
    is_recent: bool
    height_pct: float  # 0-100, against the largest day


@dataclass(frozen=True)
class MonthSpan:
    abbr: str  # "AUG"
    name: str
    days: int  # how many of the chart's days fall in this month
    label_at_end: bool = False  # see SHORT_TRAILING_MONTH_DAYS


@dataclass(frozen=True)
class Arrivals:
    days: tuple[ArrivalDay, ...]
    months: tuple[MonthSpan, ...]
    peak: ArrivalDay | None

    def to_json(self) -> dict:
        return {
            "start": self.days[0].day.isoformat() if self.days else None,
            "end": self.days[-1].day.isoformat() if self.days else None,
            "peak": {"date": self.peak.day.isoformat(), "count": self.peak.count} if self.peak else None,
            "days": [{"date": d.day.isoformat(), "count": d.count} for d in self.days],
        }


EMPTY_ARRIVALS = Arrivals(days=(), months=(), peak=None)


def build_arrivals(counts: dict[date, int], *, start: date | None, today: date) -> Arrivals:
    """One ArrivalDay per calendar day from start to today inclusive, zero
    where nothing arrived."""
    if start is None or start > today:
        return EMPTY_ARRIVALS

    span = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    scale = max((counts.get(d, 0) for d in span), default=0)
    recent_from = today - timedelta(days=RECENT_DAYS - 1)

    days = tuple(
        ArrivalDay(
            day=d,
            count=counts.get(d, 0),
            is_recent=d >= recent_from,
            height_pct=100.0 * counts.get(d, 0) / scale if scale else 0.0,
        )
        for d in span
    )
    # max() keeps the first of equal counts — the earliest peak day.
    peak = max(days, key=lambda d: d.count) if scale else None

    months: list[MonthSpan] = []
    for d in span:
        if months and months[-1].name == d.strftime("%B"):
            last = months[-1]
            months[-1] = MonthSpan(abbr=last.abbr, name=last.name, days=last.days + 1)
        else:
            months.append(MonthSpan(abbr=MONTH_ABBRS[d.month - 1], name=d.strftime("%B"), days=1))
    if len(months) > 1 and months[-1].days < SHORT_TRAILING_MONTH_DAYS:
        last = months[-1]
        months[-1] = MonthSpan(abbr=last.abbr, name=last.name, days=last.days, label_at_end=True)

    return Arrivals(days=days, months=tuple(months), peak=peak)
