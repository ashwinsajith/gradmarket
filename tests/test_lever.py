from __future__ import annotations

import json
from pathlib import Path

import requests

from gradmarket.sources import lever

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JOBS_200 = json.loads((FIXTURES / "lever_postings.json").read_text())
FULL_PAGE = [{"id": f"filler-{i}"} for i in range(lever.PAGE_LIMIT)]


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, bad_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise json.JSONDecodeError("bad json", "", 0)
        return self._json_data


def no_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(lever.time, "sleep", lambda s: calls.append(s))
    return calls


def test_fetch_single_page_success(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda url, **k: calls.append(url) or FakeResponse(200, JOBS_200)
    )

    result = lever.fetch("example")

    assert result.status_code == 200
    assert result.payload == JOBS_200
    assert result.job_count == 3
    assert len(calls) == 1
    assert calls[0].startswith(lever.GLOBAL_HOST)
    assert "skip=0" in calls[0]


def test_fetch_eu_fallback_on_404(monkeypatch):
    no_sleep(monkeypatch)
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        if url.startswith(lever.GLOBAL_HOST):
            return FakeResponse(404)
        return FakeResponse(200, JOBS_200)

    monkeypatch.setattr(requests, "get", fake_get)

    result = lever.fetch("eu-account")

    assert result.status_code == 200
    assert result.payload == JOBS_200
    assert len(calls) == 2
    assert calls[0].startswith(lever.GLOBAL_HOST)
    assert calls[1].startswith(lever.EU_HOST)


def test_fetch_multi_page_pagination(monkeypatch):
    no_sleep(monkeypatch)
    responses = [FakeResponse(200, FULL_PAGE), FakeResponse(200, JOBS_200)]
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda url, **k: calls.append(url) or responses.pop(0)
    )

    result = lever.fetch("bigboard")

    assert result.status_code == 200
    assert result.payload == FULL_PAGE + JOBS_200
    assert result.job_count == lever.PAGE_LIMIT + 3
    assert len(calls) == 2
    assert "skip=0" in calls[0]
    assert f"skip={lever.PAGE_LIMIT}" in calls[1]


def test_fetch_deduplicates_job_appearing_on_two_pages(monkeypatch):
    # skip/limit pagination re-reads by numeric offset; if the underlying
    # list shifts while paginating (plausible given INTER_PAGE_SLEEP between
    # pages), an item can cross the skip boundary and be read on two pages.
    no_sleep(monkeypatch)
    overlapping_job = {"id": "filler-99", "text": "Shifted Job (seen again on page 2)"}
    page1 = FULL_PAGE
    page2 = [overlapping_job] + JOBS_200
    responses = [FakeResponse(200, page1), FakeResponse(200, page2)]
    monkeypatch.setattr(requests, "get", lambda url, **k: responses.pop(0))

    result = lever.fetch("shifting")

    assert result.status_code == 200
    ids = [job["id"] for job in result.payload]
    assert len(ids) == len(set(ids))  # no id stored twice in the raw payload
    assert result.job_count == lever.PAGE_LIMIT + 3  # not +4 — the overlap collapsed
    # keeps the later page's version of the overlapping job
    assert [job for job in result.payload if job["id"] == "filler-99"] == [overlapping_job]


def test_fetch_pagination_cap_exceeded(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda url, **k: calls.append(url) or FakeResponse(200, FULL_PAGE)
    )

    result = lever.fetch("runaway")

    assert result.payload is None
    assert result.job_count == 0
    assert "pagination cap" in result.error
    assert len(calls) == lever.MAX_PAGES


def test_fetch_404_on_both_hosts_is_permanent_failure(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append(url) or FakeResponse(404))

    result = lever.fetch("missing")

    assert result.status_code == 404
    assert result.payload is None
    assert len(calls) == 2


def test_fetch_403_is_permanent_no_eu_fallback(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append(url) or FakeResponse(403))

    result = lever.fetch("forbidden")

    assert result.status_code == 403
    assert len(calls) == 1


def test_fetch_429_retries_then_succeeds(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    responses = [
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(200, JOBS_200),
    ]
    monkeypatch.setattr(requests, "get", lambda url, **k: responses.pop(0))

    result = lever.fetch("ratelimited")

    assert result.status_code == 200
    assert result.job_count == 3
    assert sleeps == [2.0]


def test_fetch_5xx_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append(1) or FakeResponse(503))

    result = lever.fetch("down")

    assert result.status_code == 503
    assert result.payload is None
    assert len(calls) == lever.MAX_RETRIES + 1
    assert sleeps == lever.BACKOFF_SECONDS


def test_fetch_timeout_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []

    def raise_timeout(url, **k):
        calls.append(1)
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "get", raise_timeout)

    result = lever.fetch("slow")

    assert result.status_code is None
    assert result.payload is None
    assert "timed out" in result.error
    assert len(calls) == lever.MAX_RETRIES + 1
    assert sleeps == lever.BACKOFF_SECONDS


def test_fetch_non_list_json_shape(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda url, **k: calls.append(1) or FakeResponse(200, {"jobs": []})
    )

    result = lever.fetch("wrongshape")

    assert result.status_code == 200
    assert result.payload is None
    assert "unexpected payload shape" in result.error
    assert len(calls) == 1
