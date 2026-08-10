from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gradmarket import db, ingest
from gradmarket.sources.base import FetchResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FAKE_RESULTS = {
    "good": FetchResult(status_code=200, payload={"jobs": [{"id": 1}, {"id": 2}]}, job_count=2),
    "bad_status": FetchResult(status_code=404, payload=None, job_count=0, error=None),
    "bad_network": FetchResult(status_code=None, payload=None, job_count=0, error="connection refused"),
}


def test_main_writes_a_row_per_company_and_summarizes(monkeypatch, capsys):
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_test.yaml"))
    monkeypatch.setattr(ingest, "SOURCES", {"greenhouse": SimpleNamespace(fetch=FAKE_RESULTS.__getitem__)})
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


def test_resolve_companies_file_uses_env_var_when_set(monkeypatch):
    target = FIXTURES / "companies_test.yaml"
    monkeypatch.setenv("COMPANIES_FILE", str(target))

    resolved = ingest.resolve_companies_file()

    assert resolved == target.resolve()


def test_resolve_companies_file_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("COMPANIES_FILE", raising=False)
    (tmp_path / "companies.yaml").write_text("greenhouse:\n  - foo\n")
    monkeypatch.chdir(tmp_path)

    resolved = ingest.resolve_companies_file()

    assert resolved == (tmp_path / "companies.yaml").resolve()


def test_resolve_companies_file_missing_raises_with_resolved_path(monkeypatch, tmp_path):
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("COMPANIES_FILE", str(missing))

    with pytest.raises(FileNotFoundError, match=re.escape(str(missing.resolve()))):
        ingest.resolve_companies_file()
