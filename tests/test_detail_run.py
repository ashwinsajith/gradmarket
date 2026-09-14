from __future__ import annotations

from pathlib import Path

from gradmarket import detail_run
from gradmarket.sources.workday import DetailResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def candidate(company="example", external_id="JR0001", external_path="/job/j1"):
    return {"company": company, "external_id": external_id, "external_path": external_path}


class FakeDB:
    def __init__(self, candidates):
        self.candidates = candidates
        self.inserted: list[dict] = []

    def get_connection(self):
        return self

    def init_schema(self, conn):
        pass

    def close(self):
        pass

    def get_postings_missing_detail(self, conn, *, source):
        return self.candidates

    def insert_raw_detail(self, conn, *, source, company, external_id, http_status, payload):
        self.inserted.append(
            {
                "source": source,
                "company": company,
                "external_id": external_id,
                "http_status": http_status,
                "payload": payload,
            }
        )
        return len(self.inserted)


def setup(monkeypatch, candidates, fetch_detail_fn):
    fake = FakeDB(candidates)
    monkeypatch.setattr(detail_run, "db", fake)
    monkeypatch.setattr(detail_run.workday, "fetch_detail", fetch_detail_fn)
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_workday_test.yaml"))
    sleeps = []
    monkeypatch.setattr(detail_run.time, "sleep", lambda s: sleeps.append(s))
    return fake, sleeps


def test_run_fetches_each_candidate_and_records_detail(monkeypatch):
    candidates = [candidate(external_id="JR0001"), candidate(external_id="JR0002")]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append((tenant, dc, site, external_path))
        return DetailResult(status_code=200, payload={"jobPostingInfo": {"title": "X"}})

    fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary == {"attempted": 2, "succeeded": 2, "failed": 0, "skipped_unconfigured": 0}
    assert len(fake.inserted) == 2
    assert fake.inserted[0]["http_status"] == 200
    assert fake.inserted[0]["source"] == "workday"


def test_calls_fetch_detail_with_token_and_external_path(monkeypatch):
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append((tenant, dc, site, external_path))
        return DetailResult(status_code=200, payload={})

    setup(monkeypatch, [candidate(external_path="/job/London-GB/Job_JR0001")], fake_fetch_detail)

    detail_run.run()

    assert calls == [("example", "wd503", "External", "/job/London-GB/Job_JR0001")]


def test_run_records_failure_without_crashing(monkeypatch):
    def fake_fetch_detail(tenant, dc, site, external_path):
        return DetailResult(status_code=404, payload=None, error="not found")

    fake, _sleeps = setup(monkeypatch, [candidate()], fake_fetch_detail)

    summary = detail_run.run()

    assert summary["succeeded"] == 0
    assert summary["failed"] == 1
    assert fake.inserted[0]["http_status"] == 404
    assert fake.inserted[0]["payload"] is None


def test_run_paces_between_requests_but_not_before_the_first(monkeypatch):
    candidates = [candidate(external_id=f"JR{i:04d}") for i in range(3)]
    _fake, sleeps = setup(
        monkeypatch, candidates, lambda tenant, dc, site, external_path: DetailResult(status_code=200, payload={})
    )

    detail_run.run()

    assert sleeps == [detail_run.INTER_REQUEST_SLEEP] * 2  # 3 requests, 2 gaps


def test_run_respects_limit(monkeypatch):
    candidates = [candidate(external_id=f"JR{i:04d}") for i in range(5)]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(external_path)
        return DetailResult(status_code=200, payload={})

    fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run(limit=2)

    assert summary["attempted"] == 2
    assert len(calls) == 2
    assert len(fake.inserted) == 2


def test_run_skips_company_with_no_companies_yaml_entry(monkeypatch):
    candidates = [candidate(company="not-configured", external_id="JR0001")]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped_unconfigured": 1}
    assert calls == []
    assert fake.inserted == []


def test_skipped_candidate_does_not_consume_sleep_or_limit_budget(monkeypatch):
    candidates = [
        candidate(company="not-configured", external_id="JR0000"),
        candidate(company="example", external_id="JR0001"),
    ]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    _fake, sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run(limit=1)

    # the unconfigured candidate is skipped for free; the one real request
    # still happens and still counts toward (and fits under) the limit
    assert summary["attempted"] == 1
    assert summary["skipped_unconfigured"] == 1
    assert len(calls) == 1
    assert sleeps == []  # only one actual request made — no gap to pace
