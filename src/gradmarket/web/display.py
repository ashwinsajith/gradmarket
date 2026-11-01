"""Display-only formatting for the public page. Pure functions, no I/O.

Nothing here changes stored or API data: /api/roles still serves the raw
location string, and the page's search still filters on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FALLBACK_LOCATION = "United Kingdom"

# The page only lists UK roles, so the country is noise — and it's what
# pushes most rows past one line.
_LEADING_COUNTRY_RE = re.compile(r"^united kingdom\s*(?:,|\s-\s)\s*", re.IGNORECASE)
_TRAILING_COUNTRY_RE = re.compile(r"\s*(?:,|\s-\s)\s*united kingdom$", re.IGNORECASE)
# Workday's "UK - County - Town" / "GB - Town" segment prefix.
_SEGMENT_PREFIX_RE = re.compile(r"^(?:uk|gb)\s+-\s+", re.IGNORECASE)
_DASH_SEPARATOR_RE = re.compile(r"\s+-\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_STRAY_PUNCTUATION = ",; "

# Segments that say how, not where. Kept — they're real information — but
# they aren't locations, so they never count towards "+N more".
_QUALIFIERS = {"in-office", "in office", "on-site", "onsite", "hybrid", "remote"}


@dataclass(frozen=True)
class LocationDisplay:
    short: str  # what the row shows: the first location, plus any qualifiers
    more: int  # how many further locations "+N more" stands for
    full: str  # every normalised segment, "; "-joined — for title and screen readers


def _normalise_segment(segment: str) -> str:
    segment = _WHITESPACE_RE.sub(" ", segment).strip(_STRAY_PUNCTUATION)
    segment = _LEADING_COUNTRY_RE.sub("", segment)
    segment = _TRAILING_COUNTRY_RE.sub("", segment)
    segment = _SEGMENT_PREFIX_RE.sub("", segment)
    segment = _DASH_SEPARATOR_RE.sub(", ", segment)
    return _WHITESPACE_RE.sub(" ", segment).strip(_STRAY_PUNCTUATION)


def display_location(raw: str | None) -> LocationDisplay:
    """Steps 1-2 (strip a leading/trailing "United Kingdom") run on the
    whole string first, as specified, then again per segment so a
    multi-location value like "London, United Kingdom; Leeds, United
    Kingdom" loses the country from every segment, not just the last.

    The first location is formatted the same whether or not more follow:
    "Hertfordshire, Ware; ..." shows "Hertfordshire, Ware +2 more".
    """
    text = _WHITESPACE_RE.sub(" ", raw or "").strip(_STRAY_PUNCTUATION)
    text = _LEADING_COUNTRY_RE.sub("", text)
    text = _TRAILING_COUNTRY_RE.sub("", text)

    segments: list[str] = []
    for part in text.split(";"):
        segment = _normalise_segment(part)
        if segment and segment not in segments:
            segments.append(segment)

    if not segments:
        return LocationDisplay(short=FALLBACK_LOCATION, more=0, full=FALLBACK_LOCATION)

    qualifiers = [s for s in segments if s.lower() in _QUALIFIERS]
    locations = [s for s in segments if s.lower() not in _QUALIFIERS]
    full = "; ".join(segments)

    if not locations:
        return LocationDisplay(short=" · ".join(qualifiers), more=0, full=full)

    return LocationDisplay(short=" · ".join([*qualifiers, locations[0]]), more=len(locations) - 1, full=full)
