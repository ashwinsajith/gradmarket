"""Deployment invariants for the web service: what it runs as, what it
imports, and where it finds companies.yaml."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from gradmarket.web.__main__ import server_options
from gradmarket.web.store import count_configured_boards

REPO_ROOT = Path(__file__).resolve().parents[2]

# The collector's entry points. The web service must never run any of them,
# and nothing it imports should even load them.
COLLECTOR_MODULES = {
    "gradmarket.ingest",
    "gradmarket.parse_run",
    "gradmarket.detail_run",
    "gradmarket.classify_run",
    "gradmarket.pipeline",
}


def test_server_reads_port_from_env_and_pins_one_worker_no_reload():
    options = server_options({"PORT": "4321"})

    assert options == {"host": "0.0.0.0", "port": 4321, "workers": 1, "reload": False, "log_level": "info"}


def test_server_default_port_without_env():
    assert server_options({})["port"] == 8000


def test_importing_the_web_app_loads_no_collector_module():
    # A fresh interpreter, so modules other tests imported don't count.
    script = (
        "import sys, gradmarket.web.app, gradmarket.web.__main__; "
        f"print(sorted(m for m in sys.modules if m in {sorted(COLLECTOR_MODULES)!r}))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "[]"


def test_boards_monitored_loads_companies_file_from_a_non_cwd_path(tmp_path, monkeypatch):
    elsewhere = tmp_path / "config" / "companies.yaml"
    elsewhere.parent.mkdir()
    elsewhere.write_text("greenhouse:\n  - acme\nlever:\n  - other\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)  # no companies.yaml here
    monkeypatch.setenv("COMPANIES_FILE", str(elsewhere))

    assert count_configured_boards() == 2


def test_boards_monitored_default_is_cwd_like_the_collector(monkeypatch):
    # The image's WORKDIR is /app, where companies.yaml is copied — the same
    # default the collector relies on.
    monkeypatch.delenv("COMPANIES_FILE", raising=False)
    monkeypatch.chdir(REPO_ROOT)

    assert count_configured_boards() > 0


def test_boards_monitored_fails_loudly_when_the_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPANIES_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError):
        count_configured_boards()
