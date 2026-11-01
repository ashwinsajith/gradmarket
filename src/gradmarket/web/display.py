"""Display-only formatting for the public page. Pure functions, no I/O.

Nothing here changes stored or API data: /api/roles still serves the raw
location string, and the page's search still filters on it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from gradmarket.classify.location import UK_CITIES, classify_location

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


# --- city filter ---
#
# Cities offered by the page's city filter. The classifier's UK city list,
# plus towns that recur in the data and would otherwise all land in
# "Other". Filtering only — none of these affect location_class. Omitted
# on purpose: York (inside "New York") and Bath (a word in street names).
_EXTRA_FILTER_CITIES = [
    "aberdeen",
    "coventry",
    "derby",
    "dundee",
    "exeter",
    "guildford",
    "leicester",
    "milton keynes",
    "northampton",
    "norwich",
    "plymouth",
    "southampton",
    "stevenage",
    "swindon",
]
FILTER_CITIES = [*UK_CITIES, *_EXTRA_FILTER_CITIES]
# A city name followed by a street-type word is an address, not the city:
# "New Oxford Street" (London), "London Road" (Reading), "Oxford Circus".
_STREET_WORDS = r"street|st|road|rd|avenue|ave|lane|place|square|circus|way|row|park|court|gate|wharf"
_FILTER_CITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in FILTER_CITIES) + r")\b(?!\s+(?:" + _STREET_WORDS + r")\b)",
    re.IGNORECASE,
)
_REMOTE_PATTERN = re.compile(r"\bremote\b")
_ENTRY_SPLIT_RE = re.compile(r"[;|•]")

REMOTE = "Remote"
OTHER = "Other"


def _is_uk_mention(part: str, match: re.Match[str]) -> bool:
    """Whether this town name, where it sits in the segment, is the UK one.
    Asks the location classifier about the segment with the town swapped
    for London: if nothing around it marks it foreign, that's UK. Reuses
    the classifier's own qualifier rules instead of a second set — so
    "Plymouth, MA" and "Cambridge, United States" are out, while a town
    the classifier doesn't list ("Milton Keynes") is still in. Runs on the
    original case: state codes only count in uppercase."""
    probe = part[: match.start()] + "London" + part[match.end() :]
    return classify_location(probe) == "uk"


def derive_cities(raw: str | None) -> tuple[str, ...]:
    """The cities a posting is in, for filtering — a multi-location posting
    belongs to each of them, so "Cambridge, MA; London" is London only.
    "Remote" when a segment that isn't foreign says remote; "Other" when
    nothing else matched."""
    text = (raw or "").translate(_DASH_AND_SPACE_TRANSLATION)
    cities: list[str] = []
    remote = False
    for part in _ENTRY_SPLIT_RE.split(text):
        for match in _FILTER_CITY_PATTERN.finditer(part):
            city = match.group(1).title()
            if city not in cities and _is_uk_mention(part, match):
                cities.append(city)
        if _REMOTE_PATTERN.search(part.lower()) and classify_location(part) != "non_uk":
            remote = True
    if remote:
        cities.append(REMOTE)
    return tuple(cities) or (OTHER,)


def count_cities(city_lists: Iterable[tuple[str, ...]]) -> list[tuple[str, int]]:
    """(city, postings in it), most postings first, ties alphabetical. A
    posting in two cities counts once in each."""
    counts = Counter(city for cities in city_lists for city in cities)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

