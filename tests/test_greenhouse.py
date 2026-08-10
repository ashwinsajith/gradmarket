from __future__ import annotations

import json
from pathlib import Path

import requests

from gradmarket.sources import greenhouse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JOBS_200 = json.loads((FIXTURES / "greenhouse_jobs_200.json").read_text())


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
    monkeypatch.setattr(greenhouse.time, "sleep", lambda s: calls.append(s))
    return calls


def test_fetch_200_success(monkeypatch):
    no_sleep(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, JOBS_200))

    result = greenhouse.fetch("example")

    assert result.status_code == 200
    assert result.payload == JOBS_200
    assert result.job_count == 2
    assert result.error is None


def test_fetch_200_malformed_json(monkeypatch):
    no_sleep(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, bad_json=True))

    result = greenhouse.fetch("example")

    assert result.status_code == 200
    assert result.payload is None
    assert result.job_count == 0
    assert result.error is not None


def test_fetch_404_is_permanent(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(404))

    result = greenhouse.fetch("missing")

    assert result.status_code == 404
    assert result.payload is None
    assert len(calls) == 1


def test_fetch_403_is_permanent(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(403))

    result = greenhouse.fetch("forbidden")

    assert result.status_code == 403
    assert len(calls) == 1


def test_fetch_429_retries_then_succeeds(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    responses = [
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(200, JOBS_200),
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: responses.pop(0))

    result = greenhouse.fetch("ratelimited")

    assert result.status_code == 200
    assert result.job_count == 2
    assert sleeps == [2.0]


def test_fetch_5xx_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: calls.append(1) or FakeResponse(503)
    )

    result = greenhouse.fetch("down")

    assert result.status_code == 503
    assert result.payload is None
    assert len(calls) == greenhouse.MAX_RETRIES + 1
    assert sleeps == greenhouse.BACKOFF_SECONDS


def test_fetch_timeout_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []

    def raise_timeout(*a, **k):
        calls.append(1)
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "get", raise_timeout)

    result = greenhouse.fetch("slow")

    assert result.status_code is None
    assert result.payload is None
    assert "timed out" in result.error
    assert len(calls) == greenhouse.MAX_RETRIES + 1
    assert sleeps == greenhouse.BACKOFF_SECONDS
