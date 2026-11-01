from __future__ import annotations

import pytest

from gradmarket.web.display import LocationDisplay, display_location

EXAMPLES = [
        (
            "United Kingdom, Avonmouth Customer Delivery Hub",
            LocationDisplay(short="Avonmouth Customer Delivery Hub", more=0, full="Avonmouth Customer Delivery Hub"),
        ),
        (
            "United Kingdom, GB - London, United Kingdom",
            LocationDisplay(short="London", more=0, full="London"),
        ),
        (
            "United Kingdom, UK - County Durham - Barnard Castle",
            LocationDisplay(short="County Durham, Barnard Castle", more=0, full="County Durham, Barnard Castle"),
        ),
        (
            "United Kingdom, UK - Hertfordshire - Ware; UK - Angus - Montrose; UK - Hertfordshire - Stevenage",
            LocationDisplay(
                short="Hertfordshire, Ware",
                more=2,
                full="Hertfordshire, Ware; Angus, Montrose; Hertfordshire, Stevenage",
            ),
        ),
        (
            "In-Office; London, United Kingdom",
            LocationDisplay(short="In-Office · London", more=0, full="In-Office; London"),
        ),
        ("London", LocationDisplay(short="London", more=0, full="London")),
    ]

# Every dash the separator rules must treat as the same thing. Each example
# containing a spaced " - " is re-run with each, so no variant can regress
# while another still passes.
DASH_VARIANTS = {
    "en dash": "\u2013",
    "em dash": "\u2014",
    "non-breaking hyphen": "\u2011",
    "figure dash": "\u2012",
}

DASHED_EXAMPLES = [
    *[(raw, expected) for raw, expected in EXAMPLES if " - " in raw],
    ("UK - Leeds - United Kingdom", LocationDisplay(short="Leeds", more=0, full="Leeds")),
    ("United Kingdom - Glasgow", LocationDisplay(short="Glasgow", more=0, full="Glasgow")),
    (
        "UK - London - New Oxford Street",
        LocationDisplay(short="London, New Oxford Street", more=0, full="London, New Oxford Street"),
    ),
]


@pytest.mark.parametrize(("raw", "expected"), EXAMPLES)
def test_real_location_values(raw, expected):
    assert display_location(raw) == expected


def test_single_location_needing_no_change_is_untouched():
    assert display_location("Edinburgh") == LocationDisplay(short="Edinburgh", more=0, full="Edinburgh")


@pytest.mark.parametrize("raw", ["United Kingdom", "United Kingdom, ", " ; ,", "", None])
def test_empty_after_stripping_falls_back_to_united_kingdom(raw):
    assert display_location(raw) == LocationDisplay(short="United Kingdom", more=0, full="United Kingdom")


def test_trailing_dash_country_is_stripped():
    assert display_location("UK - Leeds - United Kingdom").short == "Leeds"


def test_leading_dash_country_is_stripped():
    assert display_location("United Kingdom - Glasgow").short == "Glasgow"


def test_country_stripped_from_every_segment_not_just_the_last():
    assert display_location("London, United Kingdom; Leeds, United Kingdom").full == "London; Leeds"


def test_qualifiers_are_kept_and_never_counted_as_locations():
    loc = display_location("Hybrid; Manchester; Leeds")

    assert loc.short == "Hybrid · Manchester"
    assert loc.more == 1


def test_qualifier_only_value_shows_the_qualifier():
    assert display_location("Remote").short == "Remote"


def test_duplicate_segments_dont_inflate_more_count():
    assert display_location("London; London, United Kingdom").more == 0


def test_whitespace_and_stray_separators_are_cleaned():
    assert display_location("  ;London ,  ").short == "London"


def test_first_location_formats_the_same_with_or_without_more():
    single = display_location("United Kingdom, UK - Hertfordshire - Ware")
    multi = display_location("United Kingdom, UK - Hertfordshire - Ware; UK - Angus - Montrose")

    assert single.short == multi.short == "Hertfordshire, Ware"
    assert (single.more, multi.more) == (0, 1)


@pytest.mark.parametrize("dash_name", DASH_VARIANTS)
@pytest.mark.parametrize(("raw", "expected"), DASHED_EXAMPLES)
def test_every_dash_variant_is_a_separator(raw, expected, dash_name):
    dashed = raw.replace(" - ", f" {DASH_VARIANTS[dash_name]} ")

    assert display_location(dashed) == expected


def test_gsk_en_dash_location():
    assert display_location("UK \u2013 London \u2013 New Oxford Street").short == "London, New Oxford Street"


def test_non_breaking_spaces_around_dashes():
    assert display_location("UK\u00a0\u2013\u00a0London\u00a0\u2013\u00a0New Oxford Street").short == (
        "London, New Oxford Street"
    )


def test_non_breaking_hyphen_inside_a_qualifier():
    assert display_location("In\u2011Office; London, United Kingdom").short == "In-Office · London"


# --- mixed UK / non-UK multi-location values ---


def test_uk_segment_is_shown_first_and_full_list_keeps_original_order():
    loc = display_location("Brisbane, Queensland, Australia; London, United Kingdom")

    assert loc == LocationDisplay(short="London", more=1, full="Brisbane, Queensland, Australia; London")


def test_uk_segment_found_from_raw_text_even_without_a_listed_city():
    # Only "United Kingdom" marks the second segment as UK, and normalisation
    # strips it — UK-ness must be decided before that.
    loc = display_location("Australia, Brisbane; United Kingdom, Avonmouth Customer Delivery Hub")

    assert loc.short == "Avonmouth Customer Delivery Hub"
    assert loc.more == 1


def test_ambiguous_city_uses_the_classifier_not_the_name():
    loc = display_location("Cambridge, MA; Cambridge, England; Boston, MA")

    assert loc.short == "Cambridge, England"
    assert loc.more == 2
    assert loc.full == "Cambridge, MA; Cambridge, England; Boston, MA"


def test_first_uk_segment_wins_when_several_are_uk():
    assert display_location("Sydney, Australia; Leeds; London").short == "Leeds"


def test_qualifiers_stay_in_front_of_the_reordered_location():
    assert display_location("Hybrid; Brisbane, Australia; Leeds").short == "Hybrid · Leeds"


def test_no_uk_segment_falls_back_to_the_first_location():
    assert display_location("Brisbane, Australia; Sydney, Australia").short == "Brisbane, Australia"

