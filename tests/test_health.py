from __future__ import annotations

import requests

from gradmarket import health


def test_ping_healthcheck_skipped_when_url_unset(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or None)

    health.ping_healthcheck(failed=False)
    health.ping_healthcheck(failed=True)

    assert calls == []


def test_ping_healthcheck_success_hits_base_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping/abc")
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append((url, k)) or None)

    health.ping_healthcheck(failed=False)

    assert calls == [("https://hc.example/ping/abc", {"timeout": 10})]


def test_ping_healthcheck_failure_hits_fail_suffix(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping/abc")
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append((url, k)) or None)

    health.ping_healthcheck(failed=True)

    assert calls == [("https://hc.example/ping/abc/fail", {"timeout": 10})]


def test_ping_healthcheck_network_failure_is_swallowed_and_logged(monkeypatch, capsys):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping/abc")

    def raise_connection_error(url, **k):
        raise requests.exceptions.ConnectionError("network is unreachable")

    monkeypatch.setattr(requests, "get", raise_connection_error)

    health.ping_healthcheck(failed=False)  # must not raise

    assert "healthcheck ping failed" in capsys.readouterr().out
