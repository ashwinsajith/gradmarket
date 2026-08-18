from __future__ import annotations

import pytest
import requests

from gradmarket import classify_run, ingest, parse_run, pipeline


def _capture_pings(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append((url, k)) or None)
    return calls


def _set_healthcheck_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping/abc")


def _stub_parse_and_classify(monkeypatch):
    """Stubs both later stages with harmless fakes — every test that lets
    ingest and parse both succeed reaches classify_run.run() for real unless
    it's mocked too, and that means a real, full classification pass against
    production. Always call this (or mock classify_run yourself) whenever a
    test doesn't raise before reaching that stage."""
    monkeypatch.setattr(parse_run, "run", lambda: {"processed": 3, "skipped_failures": 0, "total_rows": 3})
    monkeypatch.setattr(classify_run, "run", lambda: {"processed": 3, "updated": 3})


def test_both_stages_succeed_pings_success_url(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 5))
    _stub_parse_and_classify(monkeypatch)

    pipeline.main()

    assert calls == [("https://hc.example/ping/abc", {"timeout": 10})]
    out = capsys.readouterr().out
    assert "ingest ok" in out
    assert "parse ok" in out
    assert "classify ok" in out


def test_healthcheck_skipped_when_url_unset(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 5))
    _stub_parse_and_classify(monkeypatch)

    pipeline.main()

    assert calls == []


def test_ingest_zero_succeeded_pings_fail_but_still_runs_parse(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 0))
    parse_calls = []
    monkeypatch.setattr(
        parse_run,
        "run",
        lambda: parse_calls.append(1) or {"processed": 2, "skipped_failures": 0, "total_rows": 2},
    )
    monkeypatch.setattr(classify_run, "run", lambda: {"processed": 2, "updated": 2})

    pipeline.main()

    assert parse_calls == [1]  # parse is still attempted — old raw_fetches may still need parsing
    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]
    out = capsys.readouterr().out
    assert "collection gap" in out
    assert "parse ok" in out
    assert "classify ok" in out


def test_ingest_raises_pings_fail_reraises_and_skips_later_stages(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (_ for _ in ()).throw(RuntimeError("db unreachable")))
    parse_calls = []
    classify_calls = []
    monkeypatch.setattr(parse_run, "run", lambda: parse_calls.append(1))
    monkeypatch.setattr(classify_run, "run", lambda: classify_calls.append(1))

    with pytest.raises(RuntimeError, match="db unreachable"):
        pipeline.main()

    assert parse_calls == []  # never attempted
    assert classify_calls == []  # never attempted
    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]
    out = capsys.readouterr().out
    assert "collection gap" in out
    assert "no new raw data collected" in out


def test_parse_raises_after_successful_ingest_pings_fail_and_distinguishes_stages(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 5))
    monkeypatch.setattr(parse_run, "run", lambda: (_ for _ in ()).throw(RuntimeError("bad hash")))
    classify_calls = []
    monkeypatch.setattr(classify_run, "run", lambda: classify_calls.append(1))

    with pytest.raises(RuntimeError, match="bad hash"):
        pipeline.main()

    assert classify_calls == []  # never attempted
    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]
    out = capsys.readouterr().out
    assert "ingest ok" in out
    assert "collection is safe" in out
    assert "parsing needs a fix" in out
    # this is a different, less urgent failure than a collection gap — the
    # log must not blur the two together
    assert "collection gap" not in out


def test_parse_raises_after_zero_succeeded_ingest_pings_fail(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 0))
    monkeypatch.setattr(parse_run, "run", lambda: (_ for _ in ()).throw(RuntimeError("also broken")))
    monkeypatch.setattr(classify_run, "run", lambda: pytest.fail("classify should not run"))

    with pytest.raises(RuntimeError, match="also broken"):
        pipeline.main()

    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]
    assert "zero boards" in capsys.readouterr().out


def test_classify_raises_after_successful_ingest_and_parse_pings_fail(monkeypatch, capsys):
    _set_healthcheck_url(monkeypatch)
    calls = _capture_pings(monkeypatch)
    monkeypatch.setattr(ingest, "run", lambda: (5, 5))
    monkeypatch.setattr(parse_run, "run", lambda: {"processed": 3, "skipped_failures": 0, "total_rows": 3})
    monkeypatch.setattr(classify_run, "run", lambda: (_ for _ in ()).throw(RuntimeError("bad regex")))

    with pytest.raises(RuntimeError, match="bad regex"):
        pipeline.main()

    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]
    out = capsys.readouterr().out
    assert "ingest ok" in out
    assert "parse ok" in out
    assert "least urgent" in out
    assert "collection gap" not in out
