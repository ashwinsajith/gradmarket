from __future__ import annotations

from gradmarket.parse import workday as parse_workday
from gradmarket.parse.base import ExtractorContext
from gradmarket.sources.workday import WorkdayToken

TOKEN = WorkdayToken(company="example", tenant="example", dc="wd503", site="External")


def context(details=None, *, token=TOKEN):
    return ExtractorContext(details_by_external_id=details or {}, token=token)


def list_item(*, title="Graduate Software Engineer", external_path="/job/j1", bullet_fields=None):
    item = {"title": title, "externalPath": external_path}
    if bullet_fields is not None:
        item["bulletFields"] = bullet_fields
    return item


def detail(*, description="<p>Join us.</p>", location="Edinburgh", additional=None, country=None, title=None):
    info = {"jobDescription": description, "location": location}
    if additional is not None:
        info["additionalLocations"] = additional
    if country is not None:
        info["country"] = {"descriptor": country}
    if title is not None:
        info["title"] = title
    return {"jobPostingInfo": info}


# --- NEEDS_DETAILS flag ---


def test_needs_details_is_true():
    assert parse_workday.NEEDS_DETAILS is True


# --- external_id derivation ---


def test_external_id_from_bullet_fields_first_element():
    item = list_item(bullet_fields=["JR0001", "some-other-field"])

    postings = parse_workday.extract([item], context())

    assert postings[0].external_id == "JR0001"


def test_external_id_falls_back_to_external_path_when_bullet_fields_absent():
    item = list_item(external_path="/job/London-GB/Graduate-Software-Engineer_JR0002")

    postings = parse_workday.extract([item], context())

    assert postings[0].external_id == "/job/London-GB/Graduate-Software-Engineer_JR0002"


def test_external_id_falls_back_to_external_path_when_bullet_fields_empty():
    # bulletFields is tenant-configurable — an empty list must not be
    # treated as "has an id", it must fall through to externalPath.
    item = list_item(external_path="/job/j3", bullet_fields=[])

    postings = parse_workday.extract([item], context())

    assert postings[0].external_id == "/job/j3"


def test_missing_external_path_skips_with_warning(capsys):
    item = {"title": "No Path Job", "bulletFields": ["JR9999"]}

    postings = parse_workday.extract([item], context())

    assert postings == []
    out = capsys.readouterr().out
    assert "missing externalPath" in out
    assert "No Path Job" in out


# --- URL construction ---


def test_url_built_from_token_and_external_path():
    item = list_item(external_path="/job/London-GB/Graduate-Software-Engineer_JR0001", bullet_fields=["JR0001"])

    postings = parse_workday.extract([item], context())

    assert postings[0].url == (
        "https://example.wd503.myworkdayjobs.com/en-US/External"
        "/job/London-GB/Graduate-Software-Engineer_JR0001"
    )


# --- detail absent: partial ParsedPosting ---


def test_detail_absent_yields_partial_posting_from_list_alone():
    item = list_item(title="Graduate Software Engineer", bullet_fields=["JR0001"])

    postings = parse_workday.extract([item], context())

    assert len(postings) == 1
    posting = postings[0]
    assert posting.external_id == "JR0001"
    assert posting.title == "Graduate Software Engineer"
    assert posting.url is not None
    assert posting.location is None
    assert posting.description_raw is None
    assert posting.department is None


def test_content_hash_changes_once_detail_arrives():
    item = list_item(title="Graduate Software Engineer", bullet_fields=["JR0001"])

    without_detail = parse_workday.extract([item], context())[0]
    with_detail = parse_workday.extract(
        [item],
        context({"JR0001": detail(description="<p>Join us.</p>", location="Edinburgh", country="United Kingdom")}),
    )[0]

    assert without_detail.content_hash != with_detail.content_hash


# --- detail present: description + location ---


def test_detail_present_fills_description_and_location():
    item = list_item(title="Graduate Software Engineer", bullet_fields=["JR0001"])
    details = {"JR0001": detail(description="<p>Join us.</p>", location="Edinburgh", country="United Kingdom")}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.description_raw == "<p>Join us.</p>"
    assert posting.title == "Graduate Software Engineer"


def test_title_falls_back_to_detail_title_when_list_title_missing():
    item = {"externalPath": "/job/j1", "bulletFields": ["JR0001"]}
    details = {"JR0001": detail(title="Detail-Only Title")}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.title == "Detail-Only Title"


# --- location: country-first format, the real Workday quirk ---


def test_location_is_country_first():
    # Real observed Workday shape: "United Kingdom, Edinburgh" — country
    # before city, the opposite order from every other source.
    item = list_item(bullet_fields=["JR0001"])
    details = {"JR0001": detail(location="Edinburgh", country="United Kingdom")}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.location == "United Kingdom, Edinburgh"


def test_location_joins_additional_locations_with_semicolon():
    item = list_item(bullet_fields=["JR0001"])
    details = {
        "JR0001": detail(location="Edinburgh", additional=["London", "Manchester"], country="United Kingdom")
    }

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.location == "United Kingdom, Edinburgh; London; Manchester"


def test_location_without_country_descriptor_falls_back_to_joined_locations():
    item = list_item(bullet_fields=["JR0001"])
    details = {"JR0001": detail(location="Edinburgh", additional=["London"], country=None)}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.location == "Edinburgh; London"


def test_location_with_only_country_descriptor_no_locations():
    item = list_item(bullet_fields=["JR0001"])
    details = {"JR0001": {"jobPostingInfo": {"jobDescription": "x", "country": {"descriptor": "United Kingdom"}}}}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.location == "United Kingdom"


def test_location_completely_absent_is_none():
    item = list_item(bullet_fields=["JR0001"])
    details = {"JR0001": {"jobPostingInfo": {"jobDescription": "x"}}}

    posting = parse_workday.extract([item], context(details))[0]

    assert posting.location is None


# --- multiple postings, mixed detail availability ---


def test_multiple_postings_mixed_detail_availability():
    items = [
        list_item(title="Has Detail", external_path="/job/j1", bullet_fields=["JR0001"]),
        list_item(title="No Detail Yet", external_path="/job/j2", bullet_fields=["JR0002"]),
    ]
    details = {"JR0001": detail(location="Edinburgh", country="United Kingdom")}

    postings = parse_workday.extract(items, context(details))

    assert len(postings) == 2
    by_id = {p.external_id: p for p in postings}
    assert by_id["JR0001"].location == "United Kingdom, Edinburgh"
    assert by_id["JR0002"].location is None
    assert by_id["JR0002"].title == "No Detail Yet"
