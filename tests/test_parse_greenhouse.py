from __future__ import annotations

import copy
import json
from pathlib import Path

from gradmarket.parse import greenhouse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "greenhouse_jobs_200.json").read_text())


def test_extract_maps_fields():
    postings = greenhouse.extract(PAYLOAD)

    assert len(postings) == 2
    first = postings[0]
    assert first.external_id == "1001"
    assert first.title == "Graduate Software Engineer"
    assert first.location == "London, UK"
    assert first.url == "https://job-boards.greenhouse.io/example/jobs/1001"
    assert first.description_raw == "<p>We are looking for a graduate engineer.</p>"
    assert first.department is None


def test_extract_uses_department_when_present():
    payload = {"jobs": [{"id": 42, "title": "Engineer", "departments": [{"name": "Engineering"}]}]}

    postings = greenhouse.extract(payload)

    assert postings[0].department == "Engineering"


def test_extract_is_hash_deterministic():
    a = greenhouse.extract(PAYLOAD)
    b = greenhouse.extract(PAYLOAD)

    assert [p.content_hash for p in a] == [p.content_hash for p in b]


def test_content_hash_changes_with_title():
    changed = copy.deepcopy(PAYLOAD)
    changed["jobs"][0]["title"] = "Different Title"

    original = greenhouse.extract(PAYLOAD)[0]
    modified = greenhouse.extract(changed)[0]

    assert original.content_hash != modified.content_hash


def test_extract_skips_job_missing_id():
    payload = {"jobs": [{"title": "No ID Job"}]}

    postings = greenhouse.extract(payload)

    assert postings == []


# --- multi-location: location.name and offices, verified against live data ---

MULTI_LOCATION_PAYLOAD = json.loads((FIXTURES / "greenhouse_multi_location.json").read_text())


def test_multi_location_offices_already_reflected_in_location_name_not_duplicated():
    postings = {p.external_id: p for p in greenhouse.extract(MULTI_LOCATION_PAYLOAD)}

    assert postings["9001"].location == "New York, New York, USA; San Francisco, California, USA"


def test_multi_location_vague_location_name_gets_offices_appended():
    postings = {p.external_id: p for p in greenhouse.extract(MULTI_LOCATION_PAYLOAD)}

    assert postings["9002"].location == "Hybrid; Austin, TX; New York, NY"


def test_multi_location_single_office_unchanged():
    postings = {p.external_id: p for p in greenhouse.extract(MULTI_LOCATION_PAYLOAD)}

    assert postings["9003"].location == "Paris, France"
