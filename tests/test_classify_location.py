from __future__ import annotations

from gradmarket.classify.location import classify_location


def test_uk_city():
    assert classify_location("London, UK") == "uk"


def test_uk_city_no_country_qualifier():
    assert classify_location("Manchester") == "uk"


def test_us_city_is_non_uk():
    assert classify_location("San Francisco, CA") == "non_uk"


def test_uk_nation_terms():
    assert classify_location("Scotland") == "uk"
    assert classify_location("England") == "uk"
    assert classify_location("Wales") == "uk"
    assert classify_location("United Kingdom") == "uk"


def test_gb_prefix():
    assert classify_location("GB-London") == "uk"


def test_uk_whole_word_with_qualifier():
    assert classify_location("Remote (UK)") == "uk"


def test_uk_word_boundary_excludes_ukraine():
    # "uk" must match as a whole word only — Ukraine starts with "Uk" but
    # isn't the UK.
    assert classify_location("Kyiv, Ukraine") == "non_uk"


def test_empty_is_unknown():
    assert classify_location("") == "unknown"


def test_none_is_unknown():
    assert classify_location(None) == "unknown"


def test_remote_alone_is_unknown():
    assert classify_location("Remote") == "unknown"


def test_hybrid_alone_is_unknown():
    assert classify_location("Hybrid") == "unknown"


def test_remote_and_hybrid_combined_is_still_unknown():
    assert classify_location("Remote; Hybrid") == "unknown"


def test_split_on_pipe():
    assert classify_location("San Francisco | London") == "uk"


def test_split_on_bullet():
    assert classify_location("San Francisco • London") == "uk"


def test_split_on_semicolon():
    assert classify_location("San Francisco; London") == "uk"


def test_split_on_comma():
    assert classify_location("San Francisco, London") == "uk"


def test_non_uk_fragment_alongside_remote_is_non_uk():
    # "Remote" alone is unknown, but paired with an actual non-UK place it
    # carries real signal.
    assert classify_location("Remote, San Francisco") == "non_uk"


# --- the three documented trap cases: ambiguous with US places / common words ---


def test_trap_cambridge_massachusetts_false_positive():
    # Documented, accepted limitation: "Cambridge" alone matches UK_CITIES
    # even when it's Cambridge, MA — no cheap way to disambiguate a bare city
    # name without a real geocoder.
    assert classify_location("Cambridge, MA") == "uk"


def test_trap_oxford_mississippi_false_positive():
    assert classify_location("Oxford, MS") == "uk"


def test_trap_reading_pennsylvania_false_positive():
    assert classify_location("Reading, PA") == "uk"
