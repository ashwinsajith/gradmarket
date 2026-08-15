from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from gradmarket import parse_run

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def gh_payload(jobs):
    return {"jobs": jobs}


def gh_job(job_id, title, location="London, UK", content="<p>desc</p>"):
    return {
        "id": job_id,
        "title": title,
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/example/jobs/{job_id}",
        "content": content,
    }


def make_row(row_id, payload, fetched_at, *, source="greenhouse", company="acme", parsed_at=None):
    return {
        "id": row_id,
        "source": source,
        "company": company,
        "http_status": 200,
        "payload": payload,
        "fetched_at": fetched_at,
        "parsed_at": parsed_at,
    }


class FakeDB:
    """In-memory stand-in for gradmarket.db's parse-related functions.

    Proves the upsert/version/close-detection *logic*; scripts/smoke_parse.py
    is what proves the SQL is actually valid against real Postgres.

    Writes apply immediately regardless of commit=, mirroring how Postgres
    makes a connection's own uncommitted writes visible to its own later
    reads in the same transaction. commit=False takes a one-time snapshot of
    everything before the first such write; rollback() restores it — the
    same "everything since the last commit disappears" guarantee dry_run
    relies on.
    """

    def __init__(self):
        self.postings: dict[tuple[str, str, str], dict] = {}
        self.posting_versions: list[dict] = []
        self.raw_fetches_history: list[dict] = []
        self._next_id = 1
        self._snapshot = None

    # --- connection / transaction control ---

    def get_connection(self):
        return self

    def close(self):
        pass

    def commit(self):
        self._snapshot = None

    def rollback(self):
        if self._snapshot is not None:
            self.postings, self.posting_versions, self.raw_fetches_history, self._next_id = self._snapshot
            self._snapshot = None

    def _ensure_snapshot(self):
        if self._snapshot is None:
            self._snapshot = copy.deepcopy(
                (self.postings, self.posting_versions, self.raw_fetches_history, self._next_id)
            )

    # --- schema / unparsed lookup ---

    def init_schema(self, conn):
        pass

    def get_unparsed_raw_fetches(self, conn):
        unparsed = [r for r in self.raw_fetches_history if r["parsed_at"] is None]
        return sorted(unparsed, key=lambda r: r["fetched_at"])

    def reset_parsed_state(self, conn, *, commit=True):
        if not commit:
            self._ensure_snapshot()
        self.postings.clear()
        self.posting_versions.clear()
        for r in self.raw_fetches_history:
            r["parsed_at"] = None
        if commit:
            self.commit()

    def mark_raw_fetch_parsed(self, conn, raw_fetch_id, *, commit=True):
        if not commit:
            self._ensure_snapshot()
        for r in self.raw_fetches_history:
            if r["id"] == raw_fetch_id:
                r["parsed_at"] = "parsed"
                break
        if commit:
            self.commit()

    # --- parsing ---

    def get_previous_raw_payload(self, conn, *, source, company, before):
        candidates = [
            r
            for r in self.raw_fetches_history
            if r["source"] == source
            and r["company"] == company
            and r["fetched_at"] < before
            and r["payload"] is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r["fetched_at"])["payload"]

    def get_existing_posting_hashes(self, conn, *, source, company):
        return {
            ext_id: row["content_hash"]
            for (src, comp, ext_id), row in self.postings.items()
            if src == source and comp == company
        }

    def bulk_upsert_postings(self, conn, *, source, company, postings, observed_at, commit=True):
        if not commit:
            self._ensure_snapshot()
        result = {}
        for p in postings:
            key = (source, company, p.external_id)
            row = self.postings.get(key)
            if row is None:
                row = {
                    "id": self._next_id,
                    "source": source,
                    "company": company,
                    "external_id": p.external_id,
                    "title": p.title,
                    "location": p.location,
                    "department": p.department,
                    "url": p.url,
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                    "is_open": True,
                    "closed_at": None,
                    "content_hash": p.content_hash,
                }
                self._next_id += 1
                self.postings[key] = row
            else:
                row["title"] = p.title
                row["location"] = p.location
                row["department"] = p.department
                row["url"] = p.url
                row["last_seen_at"] = observed_at
                row["is_open"] = True
                row["content_hash"] = p.content_hash
            result[p.external_id] = row["id"]
        if commit:
            self.commit()
        return result

    def bulk_append_posting_versions(self, conn, *, versions, commit=True):
        if not commit:
            self._ensure_snapshot()
        self.posting_versions.extend(versions)
        if commit:
            self.commit()

    def close_missing_postings(self, conn, *, source, company, seen_external_ids, closed_at, commit=True):
        if not commit:
            self._ensure_snapshot()
        count = 0
        for row in self.postings.values():
            if (
                row["source"] == source
                and row["company"] == company
                and row["is_open"]
                and row["closed_at"] is None
                and row["external_id"] not in seen_external_ids
            ):
                row["is_open"] = False
                row["closed_at"] = closed_at
                count += 1
        if commit:
            self.commit()
        return count


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(parse_run, "db", fake)
    return fake


def process(fake, row):
    fake.raw_fetches_history.append(row)
    parse_run.process_row(fake, row)


# --- the five required scenarios ---


def test_first_insert_creates_posting_and_version(fake_db):
    row = make_row(1, gh_payload([gh_job(1, "Graduate Engineer"), gh_job(2, "Data Intern")]), T0)

    process(fake_db, row)

    assert len(fake_db.postings) == 2
    posting = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting["first_seen_at"] == T0
    assert posting["last_seen_at"] == T0
    assert posting["is_open"] is True
    assert posting["closed_at"] is None
    assert len(fake_db.posting_versions) == 2


def test_repeat_observation_same_content_updates_last_seen_no_new_version(fake_db):
    process(fake_db, make_row(1, gh_payload([gh_job(1, "Graduate Engineer")]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([gh_job(1, "Graduate Engineer")]), t1))

    posting = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting["first_seen_at"] == T0
    assert posting["last_seen_at"] == t1
    assert posting["is_open"] is True
    assert len(fake_db.posting_versions) == 1


def test_content_change_appends_new_version_only_for_changed_posting(fake_db):
    process(fake_db, make_row(1, gh_payload([gh_job(1, "Graduate Engineer"), gh_job(2, "Data Intern")]), T0))

    t1 = T0 + timedelta(days=1)
    process(
        fake_db,
        make_row(2, gh_payload([gh_job(1, "Senior Graduate Engineer"), gh_job(2, "Data Intern")]), t1),
    )

    assert len(fake_db.posting_versions) == 3  # 2 initial + 1 for the changed posting
    posting1 = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting1["title"] == "Senior Graduate Engineer"
    versions_for_1 = [v for v in fake_db.posting_versions if v["posting_id"] == posting1["id"]]
    assert len(versions_for_1) == 2


def test_close_detection_marks_missing_posting_closed(fake_db):
    process(fake_db, make_row(1, gh_payload([gh_job(1, "A"), gh_job(2, "B"), gh_job(3, "C")]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([gh_job(1, "A"), gh_job(2, "B")]), t1))  # job 3 missing

    closed = fake_db.postings[("greenhouse", "acme", "3")]
    assert closed["is_open"] is False
    assert closed["closed_at"] == t1

    still_open = fake_db.postings[("greenhouse", "acme", "1")]
    assert still_open["is_open"] is True


def test_empty_feed_guard_skips_close_detection(fake_db, capsys):
    process(fake_db, make_row(1, gh_payload([gh_job(1, "A"), gh_job(2, "B"), gh_job(3, "C")]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([]), t1))  # feed suddenly empty

    for external_id in ("1", "2", "3"):
        assert fake_db.postings[("greenhouse", "acme", external_id)]["is_open"] is True

    assert "guard tripped" in capsys.readouterr().out


# --- the 50%-threshold refinement ---


def test_shrink_guard_does_not_apply_below_min_previous_count(fake_db):
    # small board: 5 -> 1 is an 80% drop, but previous < 10, so the guard
    # must not block it — otherwise real closures on small boards never fire.
    process(fake_db, make_row(1, gh_payload([gh_job(i, f"Job {i}") for i in range(1, 6)]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([gh_job(1, "Job 1")]), t1))

    for external_id in ("2", "3", "4", "5"):
        assert fake_db.postings[("greenhouse", "acme", external_id)]["is_open"] is False


def test_shrink_guard_applies_at_min_previous_count_and_over_ratio(fake_db, capsys):
    process(fake_db, make_row(1, gh_payload([gh_job(i, f"Job {i}") for i in range(1, 21)]), T0))  # 20

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([gh_job(i, f"Job {i}") for i in range(1, 6)]), t1))  # 5 (75% drop)

    for i in range(6, 21):
        assert fake_db.postings[("greenhouse", "acme", str(i))]["is_open"] is True

    assert "guard tripped" in capsys.readouterr().out


def test_empty_feed_always_trips_guard_even_below_min_previous_count(fake_db, capsys):
    # only 3 previously — below the shrink-guard's min-previous threshold —
    # but an empty feed must still trip the guard unconditionally.
    process(fake_db, make_row(1, gh_payload([gh_job(1, "A"), gh_job(2, "B"), gh_job(3, "C")]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([]), t1))

    for external_id in ("1", "2", "3"):
        assert fake_db.postings[("greenhouse", "acme", external_id)]["is_open"] is True
    assert "guard tripped" in capsys.readouterr().out


# --- closed_at written once ---


def test_closed_at_is_written_once_not_overwritten(fake_db):
    process(fake_db, make_row(1, gh_payload([gh_job(1, "A"), gh_job(2, "B")]), T0))

    t1 = T0 + timedelta(days=1)
    process(fake_db, make_row(2, gh_payload([gh_job(2, "B")]), t1))  # job 1 missing -> closes

    posting = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting["is_open"] is False
    assert posting["closed_at"] == t1

    t2 = T0 + timedelta(days=2)
    process(fake_db, make_row(3, gh_payload([gh_job(1, "A"), gh_job(2, "B")]), t2))  # job 1 reappears

    posting = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting["is_open"] is True
    assert posting["closed_at"] == t1  # unchanged by reopening

    t3 = T0 + timedelta(days=3)
    process(fake_db, make_row(4, gh_payload([gh_job(2, "B")]), t3))  # job 1 missing again

    posting = fake_db.postings[("greenhouse", "acme", "1")]
    # closed_at was already written once; the WHERE clause's closed_at IS NULL
    # guard means this later disappearance does not touch the row again.
    assert posting["closed_at"] == t1
    assert posting["is_open"] is True


# --- run() orchestration: idempotency, --full, failed-fetch handling ---


def test_run_is_idempotent_across_invocations(fake_db):
    fake_db.raw_fetches_history.append(make_row(1, gh_payload([gh_job(1, "A")]), T0))

    summary1 = parse_run.run()
    assert summary1["processed"] == 1
    assert len(fake_db.postings) == 1

    summary2 = parse_run.run()
    assert summary2["processed"] == 0
    assert len(fake_db.postings) == 1


def test_run_full_wipes_and_reprocesses_everything(fake_db):
    fake_db.raw_fetches_history.append(make_row(1, gh_payload([gh_job(1, "A")]), T0))

    parse_run.run()
    assert len(fake_db.postings) == 1
    assert len(fake_db.posting_versions) == 1

    # simulate corrupted derived state, e.g. from a since-fixed parser bug
    fake_db.postings[("greenhouse", "acme", "1")]["is_open"] = False
    fake_db.postings[("greenhouse", "acme", "1")]["closed_at"] = T0

    summary = parse_run.run(full=True)

    assert summary["processed"] == 1
    posting = fake_db.postings[("greenhouse", "acme", "1")]
    assert posting["is_open"] is True
    assert posting["closed_at"] is None
    assert len(fake_db.posting_versions) == 1  # rebuilt fresh, not appended on top of stale state


def test_run_skips_failed_fetches_without_payload(fake_db):
    fake_db.raw_fetches_history.append(
        make_row(1, None, T0, parsed_at=None) | {"http_status": 404}
    )

    summary = parse_run.run()

    assert summary["processed"] == 0
    assert summary["skipped_failures"] == 1
    assert len(fake_db.postings) == 0
    assert fake_db.raw_fetches_history[0]["parsed_at"] is not None


# --- dry_run ---


def test_dry_run_reports_changes_but_leaves_database_untouched(fake_db):
    # Seed committed history: a real (non-dry) run that's already persisted.
    process(fake_db, make_row(1, gh_payload([gh_job(1, "A"), gh_job(2, "B")]), T0))

    # A new, not-yet-parsed row: job 2 disappears, job 3 is new, job 1 is
    # unchanged. This should insert, update, close, and version something —
    # if dry_run didn't roll back, all four would show up below.
    t1 = T0 + timedelta(days=1)
    fake_db.raw_fetches_history.append(make_row(2, gh_payload([gh_job(1, "A"), gh_job(3, "C")]), t1))

    # Snapshot the fully-committed state (row 2 included, unparsed) — this is
    # what the database actually looks like right before the dry run.
    postings_before = copy.deepcopy(fake_db.postings)
    versions_before = copy.deepcopy(fake_db.posting_versions)
    raw_fetches_before = copy.deepcopy(fake_db.raw_fetches_history)

    summary = parse_run.run(dry_run=True)

    # it reports what WOULD have happened...
    assert summary["dry_run"] is True
    assert summary["processed"] == 1
    assert summary["postings_inserted"] == 1  # job 3
    assert summary["postings_updated"] == 1  # job 1
    assert summary["postings_closed"] == 1  # job 2
    assert summary["versions_appended"] == 1  # job 3 only; job 1's content didn't change

    # ...but none of it is actually there.
    assert fake_db.postings == postings_before
    assert fake_db.posting_versions == versions_before
    assert fake_db.raw_fetches_history == raw_fetches_before
    row2 = next(r for r in fake_db.raw_fetches_history if r["id"] == 2)
    assert row2["parsed_at"] is None

    # a real run afterward sees the same unparsed row and actually applies it
    real_summary = parse_run.run(dry_run=False)
    assert real_summary["processed"] == 1
    assert fake_db.postings[("greenhouse", "acme", "3")]["is_open"] is True
    assert fake_db.postings[("greenhouse", "acme", "2")]["is_open"] is False
