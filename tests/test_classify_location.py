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


# --- explicit non-UK markers beat UK city names (ambiguous US/Canadian places) ---


def test_cambridge_massachusetts_is_non_uk():
    assert classify_location("Cambridge, MA") == "non_uk"


def test_cambridge_massachusetts_workday_country_first_is_non_uk():
    # The real JLL posting: Workday prefixes the country descriptor.
    assert classify_location("United States of America, Cambridge, MA") == "non_uk"


def test_cambridge_uk():
    assert classify_location("Cambridge, UK") == "uk"
    assert classify_location("Cambridge") == "uk"
    assert classify_location("Cambridge, England") == "uk"


def test_oxford_mississippi_vs_oxford_uk():
    assert classify_location("Oxford, MS") == "non_uk"
    assert classify_location("Oxford, UK") == "uk"
    assert classify_location("Oxford") == "uk"


def test_reading_pennsylvania_vs_reading_uk():
    assert classify_location("Reading, PA") == "non_uk"
    assert classify_location("Reading, UK") == "uk"
    assert classify_location("Reading") == "uk"


def test_state_suffix_with_zip_or_trailing_country():
    assert classify_location("Boston, MA 02110") == "non_uk"
    assert classify_location("Cambridge, MA, United States") == "non_uk"


def test_canadian_province_suffix():
    # London, Ontario.
    assert classify_location("London, ON") == "non_uk"


def test_explicit_country_markers_suppress_city_match():
    for country in [
        "United States",
        "USA",
        "U.S.",
        "Canada",
        "Australia",
        "New Zealand",
        "Ireland",
        "Republic of Ireland",
        "India",
    ]:
        assert classify_location(f"Cambridge, {country}") == "non_uk", country
        assert classify_location(f"{country}, Cambridge") == "non_uk", country  # Workday country-first


def test_bare_us_is_case_sensitive():
    assert classify_location("Cambridge, US") == "non_uk"
    assert classify_location("Cambridge, us") == "uk"  # lowercase "us" is just a word


def test_country_must_be_the_whole_fragment():
    # "India" doesn't qualify a city via "Indiana", nor "Ireland" via
    # "Northern Ireland" (which is itself a UK marker).
    assert classify_location("Reading, Indiana") == "non_uk"  # the state, not India
    assert classify_location("Belfast, Northern Ireland") == "uk"


# --- a marker only overrules the city it qualifies, never the whole string ---


def test_comma_separated_city_list_with_singapore_keeps_london():
    # Commas also separate whole locations. Regression: a string-wide
    # "contains a non-UK country" rule classified these non_uk — 8 of the
    # 250 labelled rows, all genuinely London roles.
    assert classify_location("London, Montreal, Singapore") == "uk"
    assert classify_location("Montreal, London, Singapore, Warsaw, Houston") == "uk"
    assert classify_location("Geneva, London, Paris, Singapore") == "uk"
    assert classify_location("London and Singapore") == "uk"


def test_singapore_never_overrules_a_uk_city():
    # A city-state, so "the fragment after London" says nothing about London.
    assert classify_location("London, Singapore") == "uk"
    assert classify_location("Singapore") == "non_uk"


def test_country_only_qualifies_the_city_directly_before_it():
    assert classify_location("London, Toronto, Canada") == "uk"


# --- spelled-out state / province names ---


def test_spelled_out_us_state_is_a_marker():
    assert classify_location("Manchester, New Hampshire") == "non_uk"
    assert classify_location("Birmingham, Alabama") == "non_uk"
    assert classify_location("Reading, Pennsylvania") == "non_uk"
    assert classify_location("Oxford, Mississippi") == "non_uk"


def test_spelled_out_state_chained_before_country():
    assert classify_location("Cambridge, Massachusetts, United States") == "non_uk"
    assert classify_location("Cambridge, Massachusetts 02139") == "non_uk"


def test_spelled_out_canadian_province_is_a_marker():
    assert classify_location("London, Ontario") == "non_uk"
    assert classify_location("Cambridge, British Columbia") == "non_uk"


def test_state_name_must_be_the_whole_fragment():
    # Virginia Water is in Surrey; New Hampshire isn't Hampshire.
    assert classify_location("London, Virginia Water") == "uk"


def test_washington_alone_is_not_a_marker():
    # Washington is a town in Tyne and Wear, next to Newcastle.
    assert classify_location("Newcastle, Washington") == "uk"


def test_washington_with_another_us_signal_is_a_marker():
    assert classify_location("Newcastle, Washington, USA") == "non_uk"
    assert classify_location("Newcastle, Washington, United States") == "non_uk"
    assert classify_location("Newcastle, Washington 98056") == "non_uk"
    assert classify_location("Newcastle, Washington; Seattle, WA") == "non_uk"


def test_other_state_names_that_are_uk_places_need_corroboration_too():
    # New York (North Tyneside), Quebec (County Durham), Maryland (Newham),
    # California (Norfolk).
    assert classify_location("Newcastle, New York") == "uk"
    assert classify_location("Newcastle, New York, USA") == "non_uk"
    assert classify_location("London, Quebec") == "uk"
    assert classify_location("London, Quebec, Canada") == "non_uk"
    assert classify_location("London, Maryland") == "uk"
    assert classify_location("Cambridge, California 94000") == "non_uk"


def test_northern_ireland_is_uk_not_ireland():
    assert classify_location("Belfast, Northern Ireland") == "uk"
    assert classify_location("Northern Ireland") == "uk"
    assert classify_location("Dublin, Ireland") == "non_uk"


def test_non_uk_places_named_after_uk_nations():
    assert classify_location("Sydney, New South Wales, Australia") == "non_uk"
    assert classify_location("Boston, New England") == "non_uk"


def test_country_first_only_applies_to_its_own_entry():
    # Workday prefixes the PRIMARY location's country to the whole "; "-joined
    # list — the London entry is still a London role.
    assert classify_location("United States of America, Cambridge, MA; London") == "uk"
    assert classify_location("Cambridge, MA | London") == "uk"


def test_explicit_uk_marker_survives_alongside_explicit_non_uk():
    assert classify_location("London, UK / New York, USA") == "uk"
