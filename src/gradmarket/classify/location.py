"""Location classifier: pure function, no DB access.

classify_location(location) -> "uk" | "non_uk" | "unknown"

Rough on purpose — these are free-text location strings from five
different ATSes, not geocoded addresses.

A location string is split into entries (";", "|", "•" — every parser
joins multiple locations with "; ") and each entry into comma-separated
fragments. The posting is "uk" if ANY entry is. Within one entry:
  1. An explicit UK marker in any fragment (united kingdom, england,
     scotland, wales, northern ireland, "UK", "GB-") -> uk.
  2. An explicit non-UK marker overrules the UK city name it QUALIFIES —
     an explicit country always beats a city heuristic:
       - the fragment right after the city is a US state / Canadian
         province code or name, or a non-UK country ("Cambridge, MA",
         "Manchester, New Hampshire", "Cambridge, United States",
         "London, ON"). Five names that are also UK places (Washington,
         New York, Quebec, Maryland, California) only count when the
         string has another North American signal (ZIP, US/Canada, a code);
       - or the entry starts with a non-UK country (Workday's country-first
         form, "United States of America, Cambridge, MA").
  3. Any other UK city name -> uk.
A marker only overrules the city it's attached to, never the whole string:
commas also separate whole locations ("London, Montreal, Singapore"), so a
string-wide "contains a non-UK country" rule hides real London roles — it
did, on 8 of the 250 labelled rows. See docs/classifier-spec.md.
"""

from __future__ import annotations

import re

UK_CITIES = [
    "london",
    "manchester",
    "bristol",
    "edinburgh",
    "birmingham",
    "leeds",
    "glasgow",
    "cardiff",
    "belfast",
    "sheffield",
    "liverpool",
    "newcastle",
    "nottingham",
    # Ambiguous: Cambridge/Oxford/Reading are also US place names
    # (Cambridge, MA; Oxford, MS; Reading, PA), and "reading" is also an
    # ordinary English word. Word boundaries (below) stop these matching
    # *inside* another word ("Cambridgeshire", "readings"). A US/Canadian
    # state suffix or explicit non-UK country in the same entry switches
    # city matching off (NON_UK_MARKER_PATTERN, STATE_SUFFIX_PATTERN); a
    # bare "Cambridge" with neither still reads as UK — no cheap way to do
    # better without a geocoder.
    "cambridge",
    "oxford",
    "reading",
]

UK_NATION_TERMS = ["united kingdom", "england", "scotland", "wales", "northern ireland"]

# Explicit non-UK countries. A fragment must BE one of these (whole
# fragment, case-insensitive) to qualify a city — so "india" never fires
# inside "Indiana", and "Northern Ireland" isn't "Ireland". Bare "US" is
# matched case-sensitively (lowercase "us" is an English word).
#
# Singapore is deliberately absent although it's an explicit country: it's
# a city-state, listed as one city among several ("London, Montreal,
# Singapore"), where "the fragment after London" means nothing about
# London. It has no places named after UK cities, so it never needs to
# overrule one.
NON_UK_COUNTRIES = {
    "united states of america",
    "united states",
    "usa",
    "u.s.a.",
    "u.s.",
    "canada",
    "australia",
    "new zealand",
    "ireland",
    "republic of ireland",
    "india",
}

US_STATE_CODES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]
# Canadian provinces/territories: "London, ON" is London, Ontario.
CANADIAN_PROVINCE_CODES = ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]

# Spelled-out state/province names: qualify a preceding UK city the same way
# a code does ("Manchester, New Hampshire", "Cambridge, Massachusetts").
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut", "delaware",
    "district of columbia", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "west virginia", "wisconsin", "wyoming",
}
CANADIAN_PROVINCE_NAMES = {
    "alberta", "british columbia", "manitoba", "new brunswick", "newfoundland", "newfoundland and labrador",
    "nova scotia", "ontario", "prince edward island", "saskatchewan", "northwest territories", "nunavut",
    "yukon",
}
# State/province names that are ALSO UK places, so they only qualify a city
# when the string carries another North American signal (see
# _has_north_american_signal). Checked against UK place names:
#   washington  — town in Tyne and Wear (also a village in West Sussex)
#   new york    — village in North Tyneside, a few miles from Newcastle
#   quebec      — former mining village in County Durham
#   maryland    — area of Newham, east London
#   california  — village in Norfolk
# Whole-fragment matching already keeps "Virginia Water", "New Hampshire"
# vs Hampshire, and "New Jersey" vs Jersey apart; these five match exactly.
AMBIGUOUS_REGION_NAMES = {"washington", "new york", "quebec", "québec", "maryland", "california"}
# A US/Canadian country name that corroborates an ambiguous region name.
NORTH_AMERICAN_COUNTRIES = {"united states of america", "united states", "usa", "u.s.a.", "u.s.", "canada"}

# Fragments that carry no geographic signal by themselves.
NON_INFORMATIVE_FRAGMENTS = {"remote", "hybrid"}

# Entries are whole locations (every parser joins multiple locations with
# "; "; "|" and "•" appear inside some raw strings); fragments are the
# comma-separated parts of one entry — sometimes parts of one address
# ("Cambridge, MA"), sometimes separate cities ("London, Montreal").
ENTRY_SPLIT_RE = re.compile(r"[|•;]")
FRAGMENT_SPLIT_RE = re.compile(r",")
_WHITESPACE_RE = re.compile(r"\s+")

UK_CITY_PATTERN = re.compile(r"\b(" + "|".join(re.escape(c) for c in UK_CITIES) + r")\b")
# "New South Wales" (Australia) and "New England" (US) aren't Wales/England.
UK_NATION_PATTERN = re.compile(
    r"(?<!new south )(?<!new )\b(" + "|".join(re.escape(t) for t in UK_NATION_TERMS) + r")\b"
)
# A whole fragment that is a state/province code, optionally with a ZIP:
# "MA", "MA 02110". Case-sensitive, so lowercase "in"/"or"/"me" never match.
STATE_CODE_PATTERN = re.compile(
    r"(?:" + "|".join(US_STATE_CODES + CANADIAN_PROVINCE_CODES) + r")(?:\s+\d{5}(?:-\d{4})?)?"
)
_TRAILING_ZIP_RE = re.compile(r"\s+\d{5}(?:-\d{4})?$")
ZIP_PATTERN = re.compile(r"(?<!\d)\d{5}(?:-\d{4})?(?!\d)")
# Word-boundary matched so this doesn't fire inside "Ukraine" — \b after "uk"
# requires a non-word character next, and "Ukraine" continues with "r".
UK_WORD_PATTERN = re.compile(r"\buk\b")
GB_PREFIX_PATTERN = re.compile(r"^gb-")


def _has_explicit_uk_marker(fragment: str) -> bool:
    return bool(
        UK_NATION_PATTERN.search(fragment) or UK_WORD_PATTERN.search(fragment) or GB_PREFIX_PATTERN.match(fragment)
    )


def _is_non_uk_country(fragment: str) -> bool:
    return fragment == "US" or fragment.lower() in NON_UK_COUNTRIES


def _has_north_american_signal(fragments: list[str]) -> bool:
    """Any ZIP code, US/Canadian country name, or state/province code in the
    whole location string — what an ambiguous name like "Washington" needs
    before it can count as a marker."""
    return any(
        ZIP_PATTERN.search(f)
        or f == "US"
        or f.lower() in NORTH_AMERICAN_COUNTRIES
        or STATE_CODE_PATTERN.fullmatch(f)
        for f in fragments
    )


def _qualifies_as_non_uk(fragment: str, *, north_american_signal: bool) -> bool:
    """Whether this fragment, following a city name, places that city
    outside the UK. fragment keeps its original case."""
    if STATE_CODE_PATTERN.fullmatch(fragment) or _is_non_uk_country(fragment):
        return True
    name = _TRAILING_ZIP_RE.sub("", fragment).lower()
    if name in US_STATE_NAMES or name in CANADIAN_PROVINCE_NAMES:
        return True
    return name in AMBIGUOUS_REGION_NAMES and north_american_signal


def _entry_is_uk(entry: str, *, north_american_signal: bool) -> bool:
    fragments = [f.strip() for f in FRAGMENT_SPLIT_RE.split(entry) if f.strip()]
    if any(_has_explicit_uk_marker(f.lower()) for f in fragments):
        return True
    # Workday's country-first form: the leading country owns the whole entry.
    if _is_non_uk_country(fragments[0]):
        return False
    for i, fragment in enumerate(fragments):
        if not UK_CITY_PATTERN.search(fragment.lower()):
            continue
        following = fragments[i + 1] if i + 1 < len(fragments) else None
        if following is not None and _qualifies_as_non_uk(following, north_american_signal=north_american_signal):
            continue
        return True
    return False


def classify_location(location: str | None) -> str:
    if not location:
        return "unknown"

    normalised = _WHITESPACE_RE.sub(" ", location).strip()
    entries = [e.strip() for e in ENTRY_SPLIT_RE.split(normalised) if e.strip()]
    if not entries:
        return "unknown"

    all_fragments = [f.strip() for e in entries for f in FRAGMENT_SPLIT_RE.split(e) if f.strip()]
    north_american_signal = _has_north_american_signal(all_fragments)
    if any(_entry_is_uk(e, north_american_signal=north_american_signal) for e in entries):
        return "uk"

    # "Remote" or "Hybrid" alone (or only in combination with each other)
    # says nothing about where — that's unknown, not confidently non_uk.
    if all(f.lower() in NON_INFORMATIVE_FRAGMENTS for f in all_fragments):
        return "unknown"

    return "non_uk"
