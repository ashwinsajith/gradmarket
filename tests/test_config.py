from __future__ import annotations

import re
from pathlib import Path

import pytest

from gradmarket import config

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_resolve_companies_file_uses_env_var_when_set(monkeypatch):
    target = FIXTURES / "companies_test.yaml"
    monkeypatch.setenv("COMPANIES_FILE", str(target))

    resolved = config.resolve_companies_file()

    assert resolved == target.resolve()


def test_resolve_companies_file_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("COMPANIES_FILE", raising=False)
    (tmp_path / "companies.yaml").write_text("greenhouse:\n  - foo\n")
    monkeypatch.chdir(tmp_path)

    resolved = config.resolve_companies_file()

    assert resolved == (tmp_path / "companies.yaml").resolve()


def test_resolve_companies_file_missing_raises_with_resolved_path(monkeypatch, tmp_path):
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("COMPANIES_FILE", str(missing))

    with pytest.raises(FileNotFoundError, match=re.escape(str(missing.resolve()))):
        config.resolve_companies_file()


def test_load_companies_parses_yaml():
    companies = config.load_companies(FIXTURES / "companies_test.yaml")

    assert companies == {"greenhouse": ["good", "bad_status", "bad_network"]}


def test_load_companies_empty_file_returns_empty_dict(tmp_path):
    empty = tmp_path / "companies.yaml"
    empty.write_text("")

    assert config.load_companies(empty) == {}
