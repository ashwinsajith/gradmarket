from __future__ import annotations

import copy

import pytest

from gradmarket import classify_run

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-02T00:00:00+00:00"  # later than T0 — plain ISO strings, sort lexicographically


class FakeDB:
    """In-memory stand-in for gradmarket.db's classify-related functions.

    Same snapshot/rollback technique as test_parse_run.py's FakeDB: writes
    apply immediately regardless of commit=, mirroring how Postgres makes a
    connection's own uncommitted writes visible to its own later reads in
    the same transaction; commit=False takes a one-time snapshot before the
    first such write, and rollback() restores it.
    """

    def __init__(self):
        self.postings: dict[int, dict] = {}
        self._snapshot = None

    def get_connection(self):
        return self

    def close(self):
        pass

    def commit(self):
        self._snapshot = None

    def rollback(self):
        if self._snapshot is not None:
            self.postings = self._snapshot
            self._snapshot = None

    def _ensure_snapshot(self):
        if self._snapshot is None:
            self._snapshot = copy.deepcopy(self.postings)

    def init_schema(self, conn):
        pass

    def get_postings_to_classify(self, conn, *, full=False):
        def needs_classification(p):
            if full:
                return True
            if p["classified_at"] is None:
                return True
            # Mirrors db.get_postings_to_classify's OR clause: classified
            # once, but the row has been written since (updated_at, real
            # write time — never posting_versions.observed_at, which is
            # just the raw row's own fetched_at and can be arbitrarily old
            # for a re-parsed historical row; see CLAUDE.md).
            updated_at = p.get("updated_at")
            return updated_at is not None and updated_at > p["classified_at"]

        rows = (p for p in self.postings.values() if needs_classification(p))
        return sorted(
            (
                {"id": p["id"], "title": p["title"], "location": p["location"], "description_raw": p["description_raw"]}
                for p in rows
            ),
            key=lambda p: p["id"],
        )

    def bulk_update_classifications(self, conn, *, classifications, commit=True):
        if not commit:
            self._ensure_snapshot()
        for c in classifications:
            row = self.postings[c["id"]]
            row["location_class"] = c["location_class"]
            row["seniority_class"] = c["seniority_class"]
            row["classified_at"] = c["classified_at"]
        if commit:
            self.commit()
        return len(classifications)


def make_posting(id, *, title=None, location=None, description_raw=None, classified_at=None, updated_at=None):
    return {
        "id": id,
        "title": title,
        "location": location,
        "description_raw": description_raw,
        "location_class": None,
        "seniority_class": None,
        "classified_at": classified_at,
        "updated_at": updated_at,
    }


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(classify_run, "db", fake)
    return fake


def test_classifies_unclassified_postings(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK")
    fake_db.postings[2] = make_posting(2, title="Senior Engineer", location="San Francisco, CA")

    summary = classify_run.run()

    assert summary["processed"] == 2
    assert summary["updated"] == 2
    assert fake_db.postings[1]["location_class"] == "uk"
    assert fake_db.postings[1]["seniority_class"] == "early"
    assert fake_db.postings[1]["classified_at"] is not None
    assert fake_db.postings[2]["location_class"] == "non_uk"
    assert fake_db.postings[2]["seniority_class"] == "experienced"


def test_skips_already_classified_postings_without_full(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK", classified_at=T0)
    fake_db.postings[2] = make_posting(2, title="Senior Engineer", location="San Francisco, CA")

    summary = classify_run.run()

    assert summary["processed"] == 1
    assert fake_db.postings[2]["classified_at"] is not None
    # untouched — was already classified, and --full wasn't passed
    assert fake_db.postings[1]["classified_at"] == T0


def test_reclassifies_posting_updated_after_classification(fake_db):
    # e.g. a Workday posting: classified once as "unknown" while its detail
    # was still missing, then a later parse writes the posting again once
    # the real description/location arrive — classified_at IS NULL alone
    # would never see it again.
    fake_db.postings[1] = make_posting(
        1,
        title="Graduate Software Engineer",
        location="United Kingdom, Edinburgh",
        classified_at=T0,
        updated_at=T1,
    )

    summary = classify_run.run()

    assert summary["processed"] == 1
    assert fake_db.postings[1]["classified_at"] != T0
    assert fake_db.postings[1]["location_class"] == "uk"


def test_does_not_reclassify_posting_not_updated_since_classification(fake_db):
    fake_db.postings[1] = make_posting(
        1,
        title="Graduate Engineer",
        location="London, UK",
        classified_at=T1,
        updated_at=T0,  # last written before the classification — nothing's changed since
    )

    summary = classify_run.run()

    assert summary["processed"] == 0
    assert fake_db.postings[1]["classified_at"] == T1  # untouched


def test_does_not_reclassify_posting_never_written(fake_db):
    # updated_at=None models a row with no updated_at recorded at all —
    # NULL is never "newer than" classified_at.
    fake_db.postings[1] = make_posting(
        1, title="Graduate Engineer", location="London, UK", classified_at=T0, updated_at=None
    )

    summary = classify_run.run()

    assert summary["processed"] == 0
    assert fake_db.postings[1]["classified_at"] == T0


def test_reparse_of_historical_data_triggers_reclassification_despite_old_version_timestamps(fake_db):
    # The exact bug this column exists to fix: re-parsing an OLD
    # raw_fetches row (a --full rebuild, or any backfill) produces
    # posting_versions rows whose observed_at is old — it's the raw row's
    # own fetched_at, when the data was *collected*, which can be
    # arbitrarily far in the past relative to when it's being *processed*
    # right now. A staleness check keyed on that timestamp would see an
    # "old" version next to an existing classified_at and wrongly treat
    # the posting as already up to date. updated_at is set to real write
    # time on every upsert regardless of how old the source data is, so a
    # historical re-parse still correctly looks freshly written.
    fake_db.postings[1] = make_posting(
        1,
        title="Graduate Engineer",
        location="London, UK",
        classified_at=T0,
        updated_at=T1,  # written just now, by today's re-parse of a historical raw row
    )

    summary = classify_run.run()

    assert summary["processed"] == 1
    assert fake_db.postings[1]["classified_at"] != T0


def test_full_reclassifies_everything(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK", classified_at=T0)
    fake_db.postings[1]["location_class"] = "non_uk"  # simulate a stale/wrong prior classification
    fake_db.postings[1]["seniority_class"] = "experienced"

    summary = classify_run.run(full=True)

    assert summary["processed"] == 1
    assert fake_db.postings[1]["location_class"] == "uk"
    assert fake_db.postings[1]["seniority_class"] == "early"
    assert fake_db.postings[1]["classified_at"] != T0


def test_full_returns_everything_regardless_of_updated_at(fake_db):
    # --full must bypass the updated_at freshness check the same way it
    # already bypasses classified_at IS NULL — a posting not updated since
    # its classification (which would be skipped without --full) still
    # gets reclassified once --full is passed.
    fake_db.postings[1] = make_posting(
        1, title="Graduate Engineer", location="London, UK", classified_at=T1, updated_at=T0
    )

    summary = classify_run.run(full=True)

    assert summary["processed"] == 1
    assert fake_db.postings[1]["classified_at"] != T1


def test_never_touches_is_open_or_closed_at(fake_db):
    posting = make_posting(1, title="Graduate Engineer", location="London, UK")
    posting["is_open"] = False
    posting["closed_at"] = "some-old-timestamp"
    fake_db.postings[1] = posting

    classify_run.run()

    assert fake_db.postings[1]["is_open"] is False
    assert fake_db.postings[1]["closed_at"] == "some-old-timestamp"


def test_dry_run_leaves_database_untouched(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK")
    before = copy.deepcopy(fake_db.postings)

    summary = classify_run.run(dry_run=True)

    assert summary["dry_run"] is True
    assert summary["processed"] == 1
    assert summary["location_counts"] == {"uk": 1}
    assert summary["seniority_counts"] == {"early": 1}

    assert fake_db.postings == before
    assert fake_db.postings[1]["classified_at"] is None


def test_dry_run_then_real_run_actually_persists(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK")

    classify_run.run(dry_run=True)
    assert fake_db.postings[1]["classified_at"] is None  # still untouched

    classify_run.run(dry_run=False)
    assert fake_db.postings[1]["classified_at"] is not None
    assert fake_db.postings[1]["location_class"] == "uk"


def test_no_unclassified_postings_processes_nothing(fake_db):
    fake_db.postings[1] = make_posting(1, title="Graduate Engineer", location="London, UK", classified_at=T0)

    summary = classify_run.run()

    assert summary["processed"] == 0
    assert summary["updated"] == 0
    assert summary["location_counts"] == {}
    assert summary["seniority_counts"] == {}
