"""scripts/check_tokens.py isn't part of the installed package, so it's
imported here by adding scripts/ to sys.path rather than via a normal
package import — mirrors how the script itself is run directly.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest
import requests
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_tokens

from gradmarket.sources import greenhouse
from gradmarket.sources.workday import WorkdayToken


def test_case_only_name_difference_is_not_evidence_of_real():
    # "differs from the token by more than case" — same string modulo case
    # doesn't count as evidence on its own.
    assert check_tokens.workable_likely_shell("acme", "ACME", None) is True


def test_name_differing_by_more_than_case_clears_it():
    assert check_tokens.workable_likely_shell("acme", "Acme Corporation", None) is False


def test_missing_or_empty_description_is_not_evidence_of_real():
    assert check_tokens.workable_likely_shell("acme", None, None) is True
    assert check_tokens.workable_likely_shell("acme", None, "") is True
    assert check_tokens.workable_likely_shell("acme", None, "   ") is True


def test_present_description_clears_it_even_with_case_only_name():
    assert check_tokens.workable_likely_shell("acme", "ACME", "<p>We build things.</p>") is False


def test_both_fail_is_likely_shell():
    # e.g. buffer/intercom/zapier/gitlab — token-identical name, no description.
    assert check_tokens.workable_likely_shell("buffer", "buffer", "") is True
    assert check_tokens.workable_likely_shell("buffer", "Buffer", None) is True


def test_both_pass_is_not_likely_shell():
    assert check_tokens.workable_likely_shell("buffer", "Buffer Inc", "<p>Real company.</p>") is False


# --- workday: company,tenant,dc,site candidates ---


def test_read_workday_candidates_parses_four_fields(tmp_path):
    path = tmp_path / "candidates_workday.txt"
    path.write_text("iberdrola,iberdrola,wd3,Iberdrola\nlivenation,livenation,wd503,LiveNationCareers\n")

    candidates = check_tokens.read_workday_candidates(path)

    assert candidates == [
        WorkdayToken(company="iberdrola", tenant="iberdrola", dc="wd3", site="Iberdrola"),
        WorkdayToken(company="livenation", tenant="livenation", dc="wd503", site="LiveNationCareers"),
    ]


def test_read_workday_candidates_skips_blank_lines_and_comments(tmp_path):
    path = tmp_path / "candidates_workday.txt"
    path.write_text("# a comment\n\niberdrola,iberdrola,wd3,Iberdrola\n")

    candidates = check_tokens.read_workday_candidates(path)

    assert candidates == [WorkdayToken(company="iberdrola", tenant="iberdrola", dc="wd3", site="Iberdrola")]


def test_read_workday_candidates_skips_malformed_lines_with_warning(tmp_path, capsys):
    path = tmp_path / "candidates_workday.txt"
    path.write_text("iberdrola,iberdrola,wd3\niberdrola,iberdrola,wd3,Iberdrola\niberdrola,,wd3,Iberdrola\n")

    candidates = check_tokens.read_workday_candidates(path)

    assert candidates == [WorkdayToken(company="iberdrola", tenant="iberdrola", dc="wd3", site="Iberdrola")]
    err = capsys.readouterr().err
    assert err.count("skipping malformed line") == 2


def test_workday_sample_formats_title_and_location():
    payload = [
        {"title": "Graduate Software Engineer", "locationsText": "London, UK"},
        {"title": "Data Analyst", "locationsText": "Remote"},
    ]

    assert check_tokens.workday_sample(payload) == [
        "Graduate Software Engineer — London, UK",
        "Data Analyst — Remote",
    ]


def test_workday_sample_caps_at_sample_size():
    payload = [{"title": f"Job {i}", "locationsText": "London"} for i in range(10)]

    sample = check_tokens.workday_sample(payload)

    assert len(sample) == check_tokens.SAMPLE_SIZE


def test_workday_sample_handles_missing_fields():
    assert check_tokens.workday_sample([{}]) == ["? — ?"]


# --- reporting: working / empty / failed sections ---
#
# Drives main() end to end through the real greenhouse fetch(), with only
# requests.get swapped for recorded responses: greenhouse_jobs_200.json (a
# board with jobs), and greenhouse_jobs_empty_200.json / greenhouse_board_404.json
# recorded from the live API (coop's board, and a nonsense token).

FIXTURES = Path(__file__).resolve().parent / "fixtures"

RECORDED = {
    "hasjobs": (200, json.loads((FIXTURES / "greenhouse_jobs_200.json").read_text())),
    "emptyboard": (200, json.loads((FIXTURES / "greenhouse_jobs_empty_200.json").read_text())),
    "missingboard": (404, json.loads((FIXTURES / "greenhouse_board_404.json").read_text())),
}


class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = {}

    def json(self):
        return self._json_data


def _fake_get(url, *args, **kwargs):
    token = url.split("/boards/")[1].split("/")[0]
    return FakeResponse(*RECORDED[token])


@pytest.fixture
def run_greenhouse(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(greenhouse.time, "sleep", lambda s: None)
    monkeypatch.setattr(check_tokens.time, "sleep", lambda s: None)

    candidates = tmp_path / "candidates.txt"
    candidates.write_text("hasjobs\nemptyboard\nmissingboard\n")
    results_file = tmp_path / "results.csv"
    monkeypatch.setattr(
        sys, "argv",
        ["check_tokens.py", str(candidates), "--source", "greenhouse", "--results-file", str(results_file)],
    )

    check_tokens.main()
    return capsys.readouterr().out, results_file


def _section(out: str, heading_prefix: str) -> str:
    """The body of the blank-line-separated report block whose heading starts
    with heading_prefix."""
    block = next(b for b in out.split("\n\n") if b.startswith(heading_prefix))
    return block.split("\n", 1)[1]


def test_paste_block_contains_only_boards_with_jobs(run_greenhouse):
    out, _ = run_greenhouse

    paste = _section(out, "# Working tokens (greenhouse)")

    assert yaml.safe_load(paste) == ["hasjobs"]


def test_empty_board_listed_in_empty_section_with_note(run_greenhouse):
    out, _ = run_greenhouse

    empty = _section(out, "# Empty boards (1)")

    assert "emptyboard: 200, 0 jobs" in empty
    assert "exists but isn't advertising anything" in empty
    assert "different ATS" in empty
    assert "hasjobs" not in empty and "missingboard" not in empty


def test_404_listed_in_failed_section(run_greenhouse):
    out, _ = run_greenhouse

    failed = _section(out, "# Failed tokens")

    assert "missingboard: 404" in failed
    assert "emptyboard" not in failed and "hasjobs" not in failed


def test_summary_counts_each_section(run_greenhouse):
    out, _ = run_greenhouse

    assert "# Summary: 1 working, 1 empty, 1 failed" in out


def test_results_csv_records_status_and_job_count(run_greenhouse):
    _, results_file = run_greenhouse

    with results_file.open(newline="") as f:
        rows = {row["token"]: row for row in csv.DictReader(f)}

    assert {t: (r["status_code"], r["status"], r["job_count"]) for t, r in rows.items()} == {
        "hasjobs": ("200", "working", "2"),
        "emptyboard": ("200", "empty", "0"),
        "missingboard": ("404", "failed", ""),
    }


def test_results_written_before_status_column_are_read_back_by_section(tmp_path):
    path = tmp_path / "old_results.csv"
    path.write_text(
        "token,status_code,label,job_count,name,description_present,tenant,dc,site\n"
        "hasjobs,200,200,5,,,,,\n"
        "emptyboard,200,200,0,,,,,\n"
        "badpayload,200,200 (bad payload: bad json),,,,,,\n"
        "missingboard,404,404,,,,,,\n"
    )

    rows = check_tokens.load_results(path)

    assert {t: r["status"] for t, r in rows.items()} == {
        "hasjobs": "working",
        "emptyboard": "empty",
        "badpayload": "failed",
        "missingboard": "failed",
    }
