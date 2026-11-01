"""Display-only formatting for the public page. Pure functions, no I/O.

Nothing here changes stored or API data: /api/roles still serves the raw
location string, and the page's search still filters on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gradmarket.classify.location import classify_location

FALLBACK_LOCATION = "United Kingdom"

# Every dash-like character the source data uses as a separator, folded to
# hyphen-minus before any other step — GSK's locations use en dashes ("UK –
# London – New Oxford Street"), and every pattern below only knows "-".
# Non-breaking spaces travel with the same data, so they're folded too.
_DASH_AND_SPACE_TRANSLATION = str.maketrans(
    {
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u00a0": " ",  # non-breaking space
    }
)

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
    """Steps 1-2 (strip a leading/trailing "United Kingdom") run on every
    ";"-separated segment, which covers the whole string's start and end
    too — so "London, United Kingdom; Leeds, United Kingdom" loses the
    country from every segment, not just the last.

    The row shows a UK location first: the page only lists UK roles, so in
    "Brisbane, Queensland, Australia; London" the London segment is the
    relevant one, and Brisbane goes behind "+1 more". UK-ness comes from the
    location classifier, run on each segment's RAW text — after
    normalisation "United Kingdom, Avonmouth Customer Delivery Hub" is just
    "Avonmouth Customer Delivery Hub", which no longer looks British. With
    no UK segment, the first location is shown. `full` always keeps the
    original order.

    The first location is formatted the same whether or not more follow:
    "Hertfordshire, Ware; ..." shows "Hertfordshire, Ware +2 more".
    """
    text = (raw or "").translate(_DASH_AND_SPACE_TRANSLATION)

    segments: list[str] = []
    uk_segments: set[str] = set()
    for part in text.split(";"):
        segment = _normalise_segment(part)
        if not segment or segment in segments:
            continue
        segments.append(segment)
        if classify_location(part) == "uk":
            uk_segments.add(segment)

    if not segments:
        return LocationDisplay(short=FALLBACK_LOCATION, more=0, full=FALLBACK_LOCATION)

    qualifiers = [s for s in segments if s.lower() in _QUALIFIERS]
    locations = [s for s in segments if s.lower() not in _QUALIFIERS]
    full = "; ".join(segments)

    if not locations:
        return LocationDisplay(short=" · ".join(qualifiers), more=0, full=full)

    shown = next((s for s in locations if s in uk_segments), locations[0])
    return LocationDisplay(short=" · ".join([*qualifiers, shown]), more=len(locations) - 1, full=full)
