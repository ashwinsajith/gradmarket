from __future__ import annotations

import json
from pathlib import Path

from gradmarket.parse import lever

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "lever_postings.json").read_text())


def test_extract_accepts_bare_array_not_object():
    assert isinstance(PAYLOAD, list)

    postings = lever.extract(PAYLOAD)

    assert len(postings) == 3


def test_extract_maps_fields():
    postings = lever.extract(PAYLOAD)

    first = postings[0]
    assert first.external_id == "a1b2c3d4-0001-0000-0000-000000000001"
    assert first.title == "Graduate Software Engineer"
    assert first.location == "London, UK"
    assert first.department == "Product & Engineering"
    assert first.url == "https://jobs.lever.co/example/a1b2c3d4-0001"
    assert first.description_raw == "<p>We are looking for a graduate engineer.</p>"


def test_department_falls_back_to_team():
    payload = [{"id": "x", "text": "T", "categories": {"team": "Backend"}}]

    postings = lever.extract(payload)

    assert postings[0].department == "Backend"


def test_extract_is_hash_deterministic():
    a = lever.extract(PAYLOAD)
    b = lever.extract(PAYLOAD)

    assert [p.content_hash for p in a] == [p.content_hash for p in b]


def test_extract_skips_job_missing_id():
    payload = [{"text": "No ID Job"}]

    postings = lever.extract(payload)

    assert postings == []
