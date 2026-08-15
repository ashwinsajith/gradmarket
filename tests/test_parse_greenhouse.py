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
