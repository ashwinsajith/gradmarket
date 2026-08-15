from __future__ import annotations

import copy
import json
from pathlib import Path

from gradmarket.parse import ashby

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "ashby_postings.json").read_text())


def test_extract_maps_fields():
    postings = ashby.extract(PAYLOAD)

    assert len(postings) == 2
    first = postings[0]
    assert first.external_id == "34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    assert first.title == "Security Engineer, Cloud"
    assert first.location == "New York, NY (HQ)"
    assert first.department == "Engineering"
    assert first.url == "https://jobs.ashbyhq.com/example/34413f8d-26bf-4bbc-8ade-eb309a0e2245"
    assert first.description_raw.startswith("<h1>About the role</h1>")


def test_extract_falls_back_to_plain_description():
    payload = {"jobs": [{"id": "x", "title": "T", "descriptionPlain": "plain text"}]}

    postings = ashby.extract(payload)

    assert postings[0].description_raw == "plain text"


def test_extract_is_hash_deterministic():
    a = ashby.extract(PAYLOAD)
    b = ashby.extract(PAYLOAD)

    assert [p.content_hash for p in a] == [p.content_hash for p in b]


def test_content_hash_changes_with_description():
    changed = copy.deepcopy(PAYLOAD)
    changed["jobs"][0]["descriptionHtml"] = "<p>Completely different</p>"

    original = ashby.extract(PAYLOAD)[0]
    modified = ashby.extract(changed)[0]

    assert original.content_hash != modified.content_hash


def test_extract_skips_job_missing_id():
    payload = {"jobs": [{"title": "No ID Job"}]}

    postings = ashby.extract(payload)

    assert postings == []
