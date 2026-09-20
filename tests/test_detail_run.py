from __future__ import annotations

from pathlib import Path

from gradmarket import detail_run
from gradmarket.sources.workday import DetailResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def candidate(company="example", external_id="JR0001", external_path="/job/j1", title="Graduate Software Engineer"):
    # title defaults to something the early-careers filter matches, so
    # every pre-existing test here (about pacing/limits/insertion, not
    # filtering) is unaffected by the filter's default-on behaviour.
    return {"company": company, "external_id": external_id, "external_path": external_path, "title": title}


class FakeDB:
    def __init__(self, candidates):
        self.candidates = candidates
        self.inserted: list[dict] = []
        # company -> is that company's latest raw_fetches row currently
        # parsed. Defaults to True (the common case: an earlier parse pass
        # already created postings from it) unless a test overrides it —
        # e.g. to simulate "already flagged unparsed", set False.
        self.latest_row_parsed: dict[str, bool] = {}
        self.flagged_for_reparse: list[tuple[str, str]] = []

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

    def mark_latest_raw_fetch_unparsed(self, conn, *, source, company):
        was_parsed = self.latest_row_parsed.get(company, True)
        if not was_parsed:
            return False
        self.latest_row_parsed[company] = False
        self.flagged_for_reparse.append((source, company))
        return True


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

    assert summary == {
        "attempted": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped_unconfigured": 0,
        "skipped_filtered": 0,
        "flagged_for_reparse": 1,  # one company, "example", got new details this run
    }
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

    assert summary == {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_unconfigured": 1,
        "skipped_filtered": 0,
        "flagged_for_reparse": 0,
    }
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


# --- is_early_careers_title: the pure filter function ---


def test_is_early_careers_title_matches_each_keyword():
    for title in [
        "Graduate Software Engineer",
        "Summer Intern",
        "Marketing Internship",
        "Junior Developer",
        "Campus Hire",
        "Industrial Placement",
        "Graduate Trainee",
        "Software Apprentice",
        "New Grad Software Engineer",
        "Summer Analyst",
        "Early Career Software Engineer",
        "Entry Level Analyst",
        "Graduate Scheme",
        "2026 Graduate Programme",
        "Software Engineering Program",
    ]:
        assert detail_run.is_early_careers_title(title) is True, title


def test_is_early_careers_title_matches_bare_year():
    assert detail_run.is_early_careers_title("Early Careers 2027") is True


def test_is_early_careers_title_rejects_ordinary_senior_title():
    assert detail_run.is_early_careers_title("Senior Relationship Manager") is False
    assert detail_run.is_early_careers_title("Head of Engineering") is False


def test_is_early_careers_title_missing_title_is_treated_as_a_match():
    # No signal either way — fetch rather than risk silently never getting
    # a real graduate role's description.
    assert detail_run.is_early_careers_title(None) is True
    assert detail_run.is_early_careers_title("") is True


# --- detail_filter wiring into run() ---


def test_non_matching_title_is_filtered_out_without_fetching(monkeypatch):
    candidates = [candidate(title="Senior Relationship Manager")]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary == {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_unconfigured": 0,
        "skipped_filtered": 1,
        "flagged_for_reparse": 0,
    }
    assert calls == []
    assert fake.inserted == []


def test_matching_title_is_fetched(monkeypatch):
    candidates = [candidate(title="2026 Graduate Programme")]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    _fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary["attempted"] == 1
    assert summary["skipped_filtered"] == 0
    assert calls == [1]


def test_missing_title_candidate_is_fetched_not_filtered(monkeypatch):
    candidates = [candidate(title=None)]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    _fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary["attempted"] == 1
    assert summary["skipped_filtered"] == 0


def test_filtered_candidate_does_not_consume_sleep_budget(monkeypatch):
    candidates = [
        candidate(external_id="JR0000", title="Senior Relationship Manager"),
        candidate(external_id="JR0001", title="Graduate Analyst"),
    ]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    _fake, sleeps = setup(monkeypatch, candidates, fake_fetch_detail)

    summary = detail_run.run()

    assert summary["attempted"] == 1
    assert summary["skipped_filtered"] == 1
    assert sleeps == []  # only one real request made — no gap to pace


def test_detail_filter_false_fetches_regardless_of_title(monkeypatch):
    candidates = [candidate(title="Senior Relationship Manager")]
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    _fake, _sleeps = setup(monkeypatch, candidates, fake_fetch_detail)
    monkeypatch.setenv("COMPANIES_FILE", str(FIXTURES / "companies_workday_no_filter_test.yaml"))

    summary = detail_run.run()

    assert summary["attempted"] == 1
    assert summary["skipped_filtered"] == 0
    assert calls == [1]


# --- flagging a company's most recent row unparsed after new details ---


def test_no_companies_flagged_for_reparse_when_nothing_fetched(monkeypatch):
    # No candidates at all — the common case for most runs (no new Workday
    # postings needing details). Nothing fetched, nothing to join in, so
    # nothing should be flagged.
    fake, _sleeps = setup(
        monkeypatch, [], lambda tenant, dc, site, external_path: DetailResult(status_code=200, payload={})
    )

    summary = detail_run.run()

    assert summary["flagged_for_reparse"] == 0
    assert fake.flagged_for_reparse == []


def test_failed_fetch_does_not_flag_company_for_reparse(monkeypatch):
    # A 404/error result stores a raw_details row with payload=None, which
    # the extractor treats the same as "no detail yet" — nothing new for a
    # reparse to actually join in, so a failure alone must not flag it.
    fake, _sleeps = setup(
        monkeypatch,
        [candidate()],
        lambda tenant, dc, site, external_path: DetailResult(status_code=404, payload=None, error="not found"),
    )

    summary = detail_run.run()

    assert summary["flagged_for_reparse"] == 0
    assert fake.flagged_for_reparse == []


def test_successful_fetch_flags_company_once_even_with_multiple_postings(monkeypatch):
    candidates = [candidate(external_id="JR0001"), candidate(external_id="JR0002")]
    fake, _sleeps = setup(
        monkeypatch, candidates, lambda tenant, dc, site, external_path: DetailResult(status_code=200, payload={})
    )

    summary = detail_run.run()

    assert summary["flagged_for_reparse"] == 1
    assert fake.flagged_for_reparse == [("workday", "example")]  # once, not once per posting


def test_flagging_is_a_noop_when_row_already_unparsed(monkeypatch):
    fake, _sleeps = setup(
        monkeypatch, [candidate()], lambda tenant, dc, site, external_path: DetailResult(status_code=200, payload={})
    )
    fake.latest_row_parsed["example"] = False  # simulates already flagged (e.g. by an earlier step)

    summary = detail_run.run()

    assert summary["flagged_for_reparse"] == 0  # nothing NEW to flag
    assert fake.flagged_for_reparse == []


def test_dry_run_skips_flagging_but_still_fetches_and_stores(monkeypatch):
    calls = []

    def fake_fetch_detail(tenant, dc, site, external_path):
        calls.append(1)
        return DetailResult(status_code=200, payload={})

    fake, _sleeps = setup(monkeypatch, [candidate()], fake_fetch_detail)

    summary = detail_run.run(dry_run=True)

    assert calls == [1]  # the fetch itself still happens
    assert len(fake.inserted) == 1  # and is still stored
    assert summary["flagged_for_reparse"] == 0
    assert fake.flagged_for_reparse == []  # only the reparse flag is skipped
