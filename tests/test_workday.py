from __future__ import annotations

import json
from pathlib import Path

import requests

from gradmarket.sources import workday

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JOBS_LIST = json.loads((FIXTURES / "workday_jobs_list.json").read_text())
JOB_DETAIL = json.loads((FIXTURES / "workday_job_detail.json").read_text())
BAD_SITE_404 = json.loads((FIXTURES / "workday_bad_site_404.json").read_text())
BARCLAYS_FILTERED_JOBS_LIST = json.loads((FIXTURES / "workday_jobs_list_barclays_filtered.json").read_text())

TOKEN = workday.WorkdayToken(company="example", tenant="example", dc="wd503", site="External")


def test_workday_token_str_is_the_company_slug():
    # ingest.py relies on this to write raw_fetches.company without any
    # Workday-specific branching — str(token) must be the identity slug,
    # not tenant/dc/site or the dataclass repr.
    token = workday.WorkdayToken(company="livenation", tenant="livenation", dc="wd503", site="livenationcareers")

    assert str(token) == "livenation"
    assert f"{token}" == "livenation"


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
    monkeypatch.setattr(workday.time, "sleep", lambda s: calls.append(s))
    return calls


# --- fetch(): list endpoint ---


def test_fetch_single_page_success(monkeypatch):
    no_sleep(monkeypatch)
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return FakeResponse(200, JOBS_LIST)

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(TOKEN)

    assert result.status_code == 200
    assert result.payload == JOBS_LIST["jobPostings"]
    assert result.job_count == 3
    assert len(calls) == 1
    url, body, headers = calls[0]
    assert url == "https://example.wd503.myworkdayjobs.com/wday/cxs/example/External/jobs"
    assert body == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    assert headers["Accept-Language"] == "en-US"


# --- optional server-side facet filtering ---


def test_fetch_passes_facets_through_as_applied_facets(monkeypatch):
    # Fixture is a representative sample of the Barclays filtered response
    # shape (real board: 973 total, 153 when filtered to these three
    # workerSubType ids — see CLAUDE.md) — total here matches the fixture's
    # own 3 sample postings so this test isn't also exercising pagination.
    no_sleep(monkeypatch)
    token = workday.WorkdayToken(
        company="barclays",
        tenant="barclays",
        dc="wd3",
        site="External_Career_Site_Barclays",
        facets={
            "workerSubType": [
                "6139d325cdcc1001a72ce8fbe2290000",
                "6139d325cdcc1001a72ceb63d5d60001",
                "6139d325cdcc1001a72ceac9adfe0000",
            ]
        },
    )
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        return FakeResponse(200, BARCLAYS_FILTERED_JOBS_LIST)

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(token)

    assert result.status_code == 200
    assert result.payload == BARCLAYS_FILTERED_JOBS_LIST["jobPostings"]
    assert result.job_count == 3
    url, body = calls[0]
    assert url == "https://barclays.wd3.myworkdayjobs.com/wday/cxs/barclays/External_Career_Site_Barclays/jobs"
    assert body["appliedFacets"] == {
        "workerSubType": [
            "6139d325cdcc1001a72ce8fbe2290000",
            "6139d325cdcc1001a72ceb63d5d60001",
            "6139d325cdcc1001a72ceac9adfe0000",
        ]
    }


def test_fetch_sends_empty_applied_facets_when_none_configured(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(requests, "post", lambda url, json, headers, timeout: calls.append(json) or FakeResponse(200, JOBS_LIST))

    workday.fetch(TOKEN)  # TOKEN has no facets configured

    assert calls[0]["appliedFacets"] == {}


def test_fetch_passes_facets_across_pagination(monkeypatch):
    # A filtered board still paginates — every page request must carry the
    # same appliedFacets, not just the first.
    no_sleep(monkeypatch)
    token = workday.WorkdayToken(
        company="barclays", tenant="barclays", dc="wd3", site="External_Career_Site_Barclays",
        facets={"workerSubType": ["id-1"]},
    )
    full_page = {
        "total": 21,
        "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/j{i}"} for i in range(workday.PAGE_LIMIT)],
    }
    second_page = {"total": 21, "jobPostings": [{"title": "Job 21", "externalPath": "/job/j21"}]}
    responses = [FakeResponse(200, full_page), FakeResponse(200, second_page)]
    bodies = []

    def fake_post(url, json, headers, timeout):
        bodies.append(json)
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(token)

    assert result.job_count == workday.PAGE_LIMIT + 1
    assert len(bodies) == 2
    assert all(body["appliedFacets"] == {"workerSubType": ["id-1"]} for body in bodies)


def test_workday_token_facets_defaults_to_empty_dict():
    token = workday.WorkdayToken(company="example", tenant="example", dc="wd503", site="External")

    assert token.facets == {}


# --- detail_filter default depends on facets (see CLAUDE.md and __post_init__) ---


def test_detail_filter_defaults_true_when_no_facets():
    token = workday.WorkdayToken(company="lloydsbanking", tenant="lbg", dc="wd3", site="Graduate_careers")

    assert token.detail_filter is True


def test_detail_filter_defaults_false_when_facets_present():
    # The Leonardo/Sky bug: a facet-filtered feed has already been scoped
    # to early-careers roles server-side, using the employer's own
    # classification. An English-only title heuristic on top can only
    # subtract from that — Leonardo's board is Italian/Portuguese
    # ("Stage", "Tesi", "Estágio", "Jovem aprendiz"), so it matched nothing
    # and 0 of 25 postings got details fetched.
    token = workday.WorkdayToken(
        company="sky", tenant="sky", dc="wd3", site="External", facets={"workerSubType": ["id-1"]}
    )

    assert token.detail_filter is False


def test_explicit_detail_filter_true_overrides_facets_default():
    token = workday.WorkdayToken(
        company="sky",
        tenant="sky",
        dc="wd3",
        site="External",
        facets={"workerSubType": ["id-1"]},
        detail_filter=True,
    )

    assert token.detail_filter is True


def test_explicit_detail_filter_false_overrides_no_facets_default():
    token = workday.WorkdayToken(
        company="lloydsbanking", tenant="lbg", dc="wd3", site="Graduate_careers", detail_filter=False
    )

    assert token.detail_filter is False


def test_fetch_paginates_until_total_collected(monkeypatch):
    no_sleep(monkeypatch)
    full_page = {
        "total": 23,
        "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/j{i}"} for i in range(workday.PAGE_LIMIT)],
    }
    second_page = {
        "total": 23,
        "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/j{i}"} for i in range(workday.PAGE_LIMIT, 23)],
    }
    responses = [FakeResponse(200, full_page), FakeResponse(200, second_page)]
    offsets = []

    def fake_post(url, json, headers, timeout):
        offsets.append(json["offset"])
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(TOKEN)

    assert result.status_code == 200
    assert result.job_count == 23
    assert offsets == [0, workday.PAGE_LIMIT]


def test_fetch_never_requests_more_than_page_limit(monkeypatch):
    # Workday's limit is capped at 20 — asking for more silently returns an
    # empty array rather than erroring, so the request body must never ask
    # for more than PAGE_LIMIT regardless of how many postings remain.
    no_sleep(monkeypatch)
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json["limit"])
        return FakeResponse(200, {"total": 1, "jobPostings": [{"title": "Only Job"}]})

    monkeypatch.setattr(requests, "post", fake_post)

    workday.fetch(TOKEN)

    assert all(limit == 20 for limit in calls)
    assert workday.PAGE_LIMIT == 20


def test_fetch_stops_on_empty_page_despite_total_claiming_more(monkeypatch):
    # Asking beyond what's available returns an empty array silently, not an
    # error — fetch() must treat that as end-of-results rather than looping
    # forever trying to satisfy a stale/wrong `total`.
    no_sleep(monkeypatch)
    first_page = {
        "total": 999,
        "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/j{i}"} for i in range(workday.PAGE_LIMIT)],
    }
    responses = [FakeResponse(200, first_page), FakeResponse(200, {"total": 999, "jobPostings": []})]
    monkeypatch.setattr(requests, "post", lambda url, json, headers, timeout: responses.pop(0))

    result = workday.fetch(TOKEN)

    assert result.status_code == 200
    assert result.job_count == workday.PAGE_LIMIT
    assert responses == []  # both configured responses were consumed, no runaway extra calls


def test_fetch_pagination_cap_exceeded(monkeypatch):
    no_sleep(monkeypatch)
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(1)
        return FakeResponse(
            200,
            {
                "total": 10**9,
                "jobPostings": [{"title": f"Job {i}"} for i in range(workday.PAGE_LIMIT)],
            },
        )

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(TOKEN)

    assert result.payload is None
    assert result.job_count == 0
    assert "pagination cap" in result.error
    assert len(calls) == workday.MAX_PAGES


def test_fetch_bad_site_404_is_permanent_failure(monkeypatch):
    no_sleep(monkeypatch)
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(1)
        return FakeResponse(404, BAD_SITE_404)

    monkeypatch.setattr(requests, "post", fake_post)

    result = workday.fetch(TOKEN)

    assert result.status_code == 404
    assert result.payload is None
    assert len(calls) == 1  # not retried — this is a permanent misconfiguration
    assert "Job_Posting_Site_ID" in result.error
    assert "BAD_REQUEST_EXCEPTION" in result.error


def test_fetch_429_retries_then_succeeds(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    responses = [
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(200, JOBS_LIST),
    ]
    monkeypatch.setattr(requests, "post", lambda url, json, headers, timeout: responses.pop(0))

    result = workday.fetch(TOKEN)

    assert result.status_code == 200
    assert result.job_count == 3
    assert sleeps == [2.0]


def test_fetch_5xx_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "post", lambda url, json, headers, timeout: calls.append(1) or FakeResponse(503)
    )

    result = workday.fetch(TOKEN)

    assert result.status_code == 503
    assert result.payload is None
    assert len(calls) == workday.MAX_RETRIES + 1
    assert sleeps == workday.BACKOFF_SECONDS


def test_fetch_timeout_exhausts_retries(monkeypatch):
    sleeps = no_sleep(monkeypatch)
    calls = []

    def raise_timeout(url, json, headers, timeout):
        calls.append(1)
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(requests, "post", raise_timeout)

    result = workday.fetch(TOKEN)

    assert result.status_code is None
    assert result.payload is None
    assert "timed out" in result.error
    assert len(calls) == workday.MAX_RETRIES + 1
    assert sleeps == workday.BACKOFF_SECONDS


# --- fetch_detail(): single-posting endpoint ---


def test_fetch_detail_success(monkeypatch):
    no_sleep(monkeypatch)
    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers))
        return FakeResponse(200, JOB_DETAIL)

    monkeypatch.setattr(requests, "get", fake_get)

    result = workday.fetch_detail("example", "wd503", "External", "/job/London-GB/Graduate-Software-Engineer_JR0001")

    assert result.status_code == 200
    url, headers = calls[0]
    assert url == (
        "https://example.wd503.myworkdayjobs.com/wday/cxs/example/External"
        "/job/London-GB/Graduate-Software-Engineer_JR0001"
    )
    assert headers["Accept"] == "application/json"


def test_fetch_detail_strips_video_info(monkeypatch):
    no_sleep(monkeypatch)
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: FakeResponse(200, JOB_DETAIL))

    result = workday.fetch_detail("example", "wd503", "External", "/job/x")

    assert "videoInfo" not in result.payload["jobPostingInfo"]
    # nothing else in the block got dropped along with it
    assert result.payload["jobPostingInfo"]["title"] == "Graduate Software Engineer"
    assert result.payload["hiringOrganization"] == {"name": "Example Co"}
    # the source fixture itself is untouched by stripping a copy
    assert "videoInfo" in JOB_DETAIL["jobPostingInfo"]


def test_fetch_detail_strips_video_info_at_any_depth(monkeypatch):
    no_sleep(monkeypatch)
    nested = {
        "jobPostingInfo": {"title": "X"},
        "related": [{"videoInfo": {"videoUrl": "https://cdn/1"}}, {"title": "Y"}],
    }
    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: FakeResponse(200, nested))

    result = workday.fetch_detail("example", "wd503", "External", "/job/x")

    assert result.payload == {
        "jobPostingInfo": {"title": "X"},
        "related": [{}, {"title": "Y"}],
    }


def test_fetch_detail_404_is_permanent_failure(monkeypatch):
    no_sleep(monkeypatch)
    calls = []
    monkeypatch.setattr(
        requests, "get", lambda url, headers, timeout: calls.append(1) or FakeResponse(404, {"message": "not found"})
    )

    result = workday.fetch_detail("example", "wd503", "External", "/job/gone")

    assert result.status_code == 404
    assert result.payload is None
    assert len(calls) == 1


def test_fetch_detail_requires_accept_json_header(monkeypatch):
    # Without Accept: application/json, Workday serves an HTML shell instead
    # of the JSON detail payload — this asserts the header is always sent.
    no_sleep(monkeypatch)
    captured = {}

    def fake_get(url, headers, timeout):
        captured.update(headers)
        return FakeResponse(200, JOB_DETAIL)

    monkeypatch.setattr(requests, "get", fake_get)

    workday.fetch_detail("example", "wd503", "External", "/job/x")

    assert captured["Accept"] == "application/json"
