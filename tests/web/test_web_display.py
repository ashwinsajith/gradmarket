from __future__ import annotations

import pytest

from gradmarket.web.display import LocationDisplay, display_location


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
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
    ],
)
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
