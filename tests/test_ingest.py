from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from gradmarket import db, ingest
from gradmarket.sources.base import FetchResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_sources_registry_includes_all_four_with_no_ingest_changes():
    """New sources register themselves in gradmarket.sources; ingest.py never
    needs touching to pick them up — that's the interchangeability contract."""
    assert set(ingest.SOURCES) == {"greenhouse", "lever", "ashby", "workable"}


FAKE_RESULTS = {
    "good": FetchResult(status_code=200, payload={"jobs": [{"id": 1}, {"id": 2}]}, job_count=2),
    "bad_status": FetchResult(status_code=404, payload=None, job_count=0, error=None),
    "bad_network": FetchResult(status_code=None, payload=None, job_count=0, error="connection refused"),
}


def test_main_writes_a_row_per_company_and_summarizes(monkeypatch, capsys):
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_test.yaml"))
    monkeypatch.setattr(
        ingest,
        "SOURCES",
        {"greenhouse": SimpleNamespace(fetch=FAKE_RESULTS.__getitem__, INTER_REQUEST_SLEEP=0)},
    )
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)

    monkeypatch.setattr(db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "init_schema", lambda conn: None)

    inserted = []

    def fake_insert(conn, *, source, company, http_status, payload):
        inserted.append(
            {"source": source, "company": company, "http_status": http_status, "payload": payload}
        )
        return len(inserted)

    monkeypatch.setattr(db, "insert_raw_fetch", fake_insert)

    ingest.main()

    assert len(inserted) == 3

    by_company = {row["company"]: row for row in inserted}
    assert by_company["good"]["source"] == "greenhouse"
    assert by_company["good"]["http_status"] == 200
    assert by_company["good"]["payload"] == {"jobs": [{"id": 1}, {"id": 2}]}

    assert by_company["bad_status"]["http_status"] == 404
    assert by_company["bad_status"]["payload"] is None

    assert by_company["bad_network"]["http_status"] is None
    assert by_company["bad_network"]["payload"] is None

    out = capsys.readouterr().out
    assert "attempted: 3" in out
    assert "succeeded: 1" in out
    assert "failed:    2" in out
    assert "total jobs seen: 2" in out


def test_run_returns_attempted_and_succeeded_counts(monkeypatch):
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_test.yaml"))
    monkeypatch.setattr(
        ingest,
        "SOURCES",
        {"greenhouse": SimpleNamespace(fetch=FAKE_RESULTS.__getitem__, INTER_REQUEST_SLEEP=0)},
    )
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    monkeypatch.setattr(db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "init_schema", lambda conn: None)
    monkeypatch.setattr(db, "insert_raw_fetch", lambda conn, **kw: None)

    attempted, succeeded = ingest.run()

    assert attempted == 3
    assert succeeded == 1


def test_source_modules_declare_their_own_inter_request_sleep():
    from gradmarket.sources import ashby, greenhouse, lever, workable

    assert greenhouse.INTER_REQUEST_SLEEP == 1
    assert lever.INTER_REQUEST_SLEEP == 1
    assert ashby.INTER_REQUEST_SLEEP == 1
    assert workable.INTER_REQUEST_SLEEP == 5


def test_run_sleeps_using_each_sources_own_inter_request_sleep(monkeypatch):
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_test_two_sources.yaml"))
    fake_sources = {
        "greenhouse": SimpleNamespace(
            fetch=lambda token: FetchResult(status_code=200, payload={"jobs": []}, job_count=0),
            INTER_REQUEST_SLEEP=1,
        ),
        "workable": SimpleNamespace(
            fetch=lambda token: FetchResult(status_code=200, payload={"jobs": []}, job_count=0),
            INTER_REQUEST_SLEEP=5,
        ),
    }
    monkeypatch.setattr(ingest, "SOURCES", fake_sources)
    sleeps = []
    monkeypatch.setattr(ingest.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "init_schema", lambda conn: None)
    monkeypatch.setattr(db, "insert_raw_fetch", lambda conn, **kw: None)

    ingest.run()

    # greenhouse: g1, g2 (sleep 1 each); workable: w1 (sleep 5), w2 (last, no sleep).
    assert sleeps == [1, 1, 5]


def test_circuit_breaker_skips_remaining_tokens_after_consecutive_429s(monkeypatch):
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_test_circuit_breaker.yaml"))

    greenhouse_calls = []
    workable_calls = []

    def fake_greenhouse_fetch(token):
        greenhouse_calls.append(token)
        return FetchResult(status_code=200, payload={"jobs": []}, job_count=0)

    def fake_workable_fetch(token):
        workable_calls.append(token)
        return FetchResult(status_code=429, payload=None, job_count=0, error=None)

    fake_sources = {
        "greenhouse": SimpleNamespace(fetch=fake_greenhouse_fetch, INTER_REQUEST_SLEEP=0),
        "workable": SimpleNamespace(fetch=fake_workable_fetch, INTER_REQUEST_SLEEP=0),
    }
    monkeypatch.setattr(ingest, "SOURCES", fake_sources)
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    monkeypatch.setattr(db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(db, "init_schema", lambda conn: None)

    inserted = []
    monkeypatch.setattr(db, "insert_raw_fetch", lambda conn, **kw: inserted.append(kw))

    attempted, succeeded = ingest.run()

    # Breaker trips after 3 consecutive 429s (CIRCUIT_BREAKER_THRESHOLD) —
    # w4/w5 are never actually fetched.
    assert workable_calls == ["w1", "w2", "w3"]
    # A different source is unaffected and runs to completion.
    assert greenhouse_calls == ["gh1"]

    assert attempted == 6  # gh1 + w1..w5, including the two skipped
    assert succeeded == 1

    by_company = {row["company"]: row for row in inserted}
    assert by_company["w3"]["http_status"] == 429
    assert by_company["w4"]["http_status"] is None
    assert by_company["w4"]["payload"] is None
    assert by_company["w5"]["http_status"] is None
