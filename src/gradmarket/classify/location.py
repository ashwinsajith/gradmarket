"""Location classifier: pure function, no DB access.

classify_location(location) -> "uk" | "non_uk" | "unknown"

Rough on purpose — these are free-text location strings from three
different ATSes, not geocoded addresses.
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
    # Ambiguous: Cambridge/Oxford are also US place names (Cambridge, MA;
    # Oxford, MS), and "reading" is also an ordinary English word. Word
    # boundaries (below) stop these from matching *inside* another word
    # (e.g. won't fire on "Cambridgeshire" or mid-word "readings"), but a
    # bare "Cambridge, MA" or "Oxford, MS" fragment with no further context
    # still misclassifies as UK — there's no cheap way to disambiguate a
    # city name from free text without a real geocoder. Known, accepted risk.
    "cambridge",
    "oxford",
    "reading",
]

UK_NATION_TERMS = ["united kingdom", "england", "scotland", "wales"]

# Fragments that carry no geographic signal by themselves.
NON_INFORMATIVE_FRAGMENTS = {"remote", "hybrid"}

FRAGMENT_SPLIT_RE = re.compile(r"[|•;,]")
_WHITESPACE_RE = re.compile(r"\s+")

UK_CITY_PATTERN = re.compile(r"\b(" + "|".join(re.escape(c) for c in UK_CITIES) + r")\b")
UK_NATION_PATTERN = re.compile(r"\b(" + "|".join(re.escape(t) for t in UK_NATION_TERMS) + r")\b")
# Word-boundary matched so this doesn't fire inside "Ukraine" — \b after "uk"
# requires a non-word character next, and "Ukraine" continues with "r".
UK_WORD_PATTERN = re.compile(r"\buk\b")
GB_PREFIX_PATTERN = re.compile(r"^gb-")


def _fragment_is_uk(fragment: str) -> bool:
    return bool(
        UK_CITY_PATTERN.search(fragment)
        or UK_NATION_PATTERN.search(fragment)
        or UK_WORD_PATTERN.search(fragment)
        or GB_PREFIX_PATTERN.match(fragment)
    )


def classify_location(location: str | None) -> str:
    if not location:
        return "unknown"

    normalised = _WHITESPACE_RE.sub(" ", location).strip().lower()
    if not normalised:
        return "unknown"

    fragments = [f.strip() for f in FRAGMENT_SPLIT_RE.split(normalised) if f.strip()]
    if not fragments:
        return "unknown"

    if any(_fragment_is_uk(f) for f in fragments):
        return "uk"

    # "Remote" or "Hybrid" alone (or only in combination with each other)
    # says nothing about where — that's unknown, not confidently non_uk.
    if all(f in NON_INFORMATIVE_FRAGMENTS for f in fragments):
        return "unknown"

    return "non_uk"
