from __future__ import annotations

import copy
import json
from pathlib import Path

from gradmarket.parse import workable

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "workable_postings.json").read_text())


def test_extract_maps_fields():
    postings = workable.extract(PAYLOAD)

    assert len(postings) == 2
    first = postings[0]
    assert first.external_id == "AB12CD34"
    assert first.title == "Graduate Software Engineer"
    assert first.location == "London, United Kingdom"
    assert first.department == "Engineering"
    assert first.url == "https://apply.workable.com/example/j/AB12CD34"
    assert first.description_raw == "<p>We are looking for a graduate engineer.</p>"


def test_extract_uses_shortcode_as_external_id_not_id():
    # Workable jobs have no "id" field at all — shortcode is the identifier.
    payload = {"jobs": [{"shortcode": "XYZ999", "title": "T", "locations": []}]}

    postings = workable.extract(payload)

    assert postings[0].external_id == "XYZ999"


def test_extract_is_hash_deterministic():
    a = workable.extract(PAYLOAD)
    b = workable.extract(PAYLOAD)

    assert [p.content_hash for p in a] == [p.content_hash for p in b]


def test_content_hash_changes_with_description():
    changed = copy.deepcopy(PAYLOAD)
    changed["jobs"][0]["description"] = "<p>Completely different</p>"

    original = workable.extract(PAYLOAD)[0]
    modified = workable.extract(changed)[0]

    assert original.content_hash != modified.content_hash


def test_extract_skips_job_missing_shortcode():
    payload = {"jobs": [{"title": "No Shortcode Job", "locations": []}]}

    postings = workable.extract(payload)

    assert postings == []


def test_extract_handles_missing_locations_key():
    payload = {"jobs": [{"shortcode": "X1", "title": "T"}]}

    postings = workable.extract(payload)

    assert postings[0].location is None


def test_extract_handles_empty_locations_array():
    payload = {"jobs": [{"shortcode": "X1", "title": "T", "locations": []}]}

    postings = workable.extract(payload)

    assert postings[0].location is None


# --- multi-location: synthetic, since no live example was found during verification ---

MULTI_LOCATION_PAYLOAD = json.loads((FIXTURES / "workable_multi_location.json").read_text())


def test_multi_location_combines_all_entries_in_order():
    postings = {p.external_id: p for p in workable.extract(MULTI_LOCATION_PAYLOAD)}

    assert postings["MULTI001"].location == (
        "London, United Kingdom; New York, United States; Toronto, Canada"
    )


def test_multi_location_includes_hidden_entries():
    # "hidden" only controls visibility on Workable's own careers page — we
    # store raw data, not a published-page filter, so a hidden location is
    # still included.
    postings = {p.external_id: p for p in workable.extract(MULTI_LOCATION_PAYLOAD)}

    assert "Toronto, Canada" in postings["MULTI001"].location


def test_multi_location_single_entry_array_unchanged():
    postings = {p.external_id: p for p in workable.extract(MULTI_LOCATION_PAYLOAD)}

    assert postings["SINGLE01"].location == "London, United Kingdom"
