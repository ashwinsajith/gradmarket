"""scripts/prune_raw_fetches.py isn't part of the installed package, so it's
imported here by adding scripts/ to sys.path rather than via a normal
package import — mirrors how the script itself is run directly (same
convention as tests/test_check_tokens.py).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prune_raw_fetches as prune

from gradmarket.parse import ashby as parse_ashby
from gradmarket.parse import greenhouse as parse_greenhouse
from gradmarket.parse import lever as parse_lever
from gradmarket.parse import workable as parse_workable

# --- strip_descriptions: per-source field handling ---


def test_strip_greenhouse_content():
    payload = {
        "jobs": [
            {"id": 1, "title": "A", "location": {"name": "London"}, "absolute_url": "u1", "content": "<p>desc</p>"},
        ]
    }

    stripped, changed = prune.strip_descriptions(payload, "greenhouse")

    assert changed is True
    assert stripped["jobs"][0]["content"] is None
    assert stripped["jobs"][0]["title"] == "A"
    assert stripped["jobs"][0]["location"] == {"name": "London"}
    assert stripped["jobs"][0]["id"] == 1
    # original untouched
    assert payload["jobs"][0]["content"] == "<p>desc</p>"


def test_strip_ashby_strips_both_html_and_plain():
    payload = {
        "jobs": [
            {
                "id": "a1",
                "title": "A",
                "location": "London",
                "jobUrl": "u1",
                "descriptionHtml": "<h1>desc</h1>",
                "descriptionPlain": "desc",
            }
        ]
    }

    stripped, changed = prune.strip_descriptions(payload, "ashby")

    assert changed is True
    assert stripped["jobs"][0]["descriptionHtml"] is None
    assert stripped["jobs"][0]["descriptionPlain"] is None
    assert stripped["jobs"][0]["title"] == "A"


def test_strip_lever_bare_array_strips_both_description_fields():
    payload = [
        {
            "id": "l1",
            "text": "A",
            "categories": {"location": "London"},
            "hostedUrl": "u1",
            "description": "<p>desc</p>",
            "descriptionPlain": "desc",
        }
    ]

    stripped, changed = prune.strip_descriptions(payload, "lever")

    assert changed is True
    assert isinstance(stripped, list)  # array structure preserved, not wrapped
    assert stripped[0]["description"] is None
    assert stripped[0]["descriptionPlain"] is None
    assert stripped[0]["text"] == "A"


def test_strip_workable_content():
    payload = {
        "jobs": [
            {"title": "A", "shortcode": "SC1", "url": "u1", "locations": [], "description": "<p>desc</p>"},
        ],
        "name": "Some Account",
    }

    stripped, changed = prune.strip_descriptions(payload, "workable")

    assert changed is True
    assert stripped["jobs"][0]["description"] is None
    assert stripped["jobs"][0]["shortcode"] == "SC1"
    assert stripped["name"] == "Some Account"  # other top-level keys preserved


def test_workday_is_a_no_op_no_description_field_in_raw_fetches():
    payload = [{"title": "A", "externalPath": "/job/j1", "bulletFields": ["JR1"]}]

    stripped, changed = prune.strip_descriptions(payload, "workday")

    assert changed is False
    assert stripped == payload


def test_unrecognised_source_is_a_no_op():
    payload = {"jobs": [{"id": 1, "content": "x"}]}

    stripped, changed = prune.strip_descriptions(payload, "some_future_source")

    assert changed is False
    assert stripped == payload


def test_already_pruned_row_is_a_no_op():
    payload = {"jobs": [{"id": 1, "title": "A", "content": None}]}

    _stripped, changed = prune.strip_descriptions(payload, "greenhouse")

    assert changed is False


def test_missing_description_field_entirely_is_a_no_op():
    payload = {"jobs": [{"id": 1, "title": "A"}]}  # no "content" key at all

    _stripped, changed = prune.strip_descriptions(payload, "greenhouse")

    assert changed is False


def test_empty_jobs_list_is_a_no_op():
    assert prune.strip_descriptions({"jobs": []}, "greenhouse") == ({"jobs": []}, False)
    assert prune.strip_descriptions([], "lever") == ([], False)


def test_non_dict_job_entries_are_left_alone():
    payload = {"jobs": [{"id": 1, "content": "x"}, "not-a-dict"]}

    stripped, changed = prune.strip_descriptions(payload, "greenhouse")

    assert changed is True
    assert stripped["jobs"][1] == "not-a-dict"


# --- identity is preserved through the real extractors (the core requirement) ---


def test_greenhouse_identity_preserved_after_stripping():
    payload = {
        "jobs": [
            {"id": 1, "title": "A", "location": {"name": "London"}, "absolute_url": "u1", "content": "<p>d1</p>"},
            {"id": 2, "title": "B", "location": {"name": "London"}, "absolute_url": "u2", "content": "<p>d2</p>"},
        ]
    }
    stripped, _ = prune.strip_descriptions(payload, "greenhouse")

    before_ids = {p.external_id for p in parse_greenhouse.extract(payload)}
    after_ids = {p.external_id for p in parse_greenhouse.extract(stripped)}
    assert before_ids == after_ids == {"1", "2"}


def test_ashby_identity_preserved_after_stripping():
    payload = {
        "jobs": [
            {
                "id": "a1",
                "title": "A",
                "location": "London",
                "jobUrl": "u1",
                "descriptionHtml": "<h1>d1</h1>",
                "descriptionPlain": "d1",
            },
            {
                "id": "a2",
                "title": "B",
                "location": "London",
                "jobUrl": "u2",
                "descriptionHtml": "<h1>d2</h1>",
                "descriptionPlain": "d2",
            },
        ]
    }
    stripped, _ = prune.strip_descriptions(payload, "ashby")

    before_ids = {p.external_id for p in parse_ashby.extract(payload)}
    after_ids = {p.external_id for p in parse_ashby.extract(stripped)}
    assert before_ids == after_ids == {"a1", "a2"}


def test_lever_identity_preserved_after_stripping():
    payload = [
        {"id": "l1", "text": "A", "categories": {}, "hostedUrl": "u1", "description": "<p>d1</p>"},
        {"id": "l2", "text": "B", "categories": {}, "hostedUrl": "u2", "description": "<p>d2</p>"},
    ]
    stripped, _ = prune.strip_descriptions(payload, "lever")

    before_ids = {p.external_id for p in parse_lever.extract(payload)}
    after_ids = {p.external_id for p in parse_lever.extract(stripped)}
    assert before_ids == after_ids == {"l1", "l2"}


def test_workable_identity_preserved_after_stripping():
    payload = {
        "jobs": [
            {"title": "A", "shortcode": "SC1", "url": "u1", "locations": [], "description": "<p>d1</p>"},
            {"title": "B", "shortcode": "SC2", "url": "u2", "locations": [], "description": "<p>d2</p>"},
        ]
    }
    stripped, _ = prune.strip_descriptions(payload, "workable")

    before_ids = {p.external_id for p in parse_workable.extract(payload)}
    after_ids = {p.external_id for p in parse_workable.extract(stripped)}
    assert before_ids == after_ids == {"SC1", "SC2"}


# --- find_candidates: SQL construction ---


class FakeCursorFind:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.sql = sql
        self.conn.params = params

    @property
    def description(self):
        return [("id",), ("source",), ("payload",), ("size_before",)]

    def fetchall(self):
        return self.conn.fetchall_result


class FakeConnFind:
    def __init__(self, fetchall_result):
        self.fetchall_result = fetchall_result
        self.sql = None
        self.params = None

    def cursor(self):
        return FakeCursorFind(self)


def test_find_candidates_excludes_workday_and_unknown_sources():
    conn = FakeConnFind(fetchall_result=[(1, "greenhouse", {"jobs": []}, 42)])
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)

    rows = prune.find_candidates(conn, cutoff=cutoff)

    assert rows == [{"id": 1, "source": "greenhouse", "payload": {"jobs": []}, "size_before": 42}]
    passed_cutoff, passed_sources = conn.params
    assert passed_cutoff == cutoff
    assert set(passed_sources) == {"greenhouse", "ashby", "lever", "workable"}
    assert "workday" not in passed_sources


# --- apply_updates: chunking + Jsonb wrapping + commit-per-chunk ---


class FakeCursorUpdate:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.calls.append(list(params))


class FakeConnUpdate:
    def __init__(self):
        self.calls: list[list] = []
        self.commits = 0

    def cursor(self):
        return FakeCursorUpdate(self)

    def commit(self):
        self.commits += 1


def test_apply_updates_chunks_and_commits_per_chunk(monkeypatch):
    monkeypatch.setattr(prune, "CHUNK_SIZE", 2)
    conn = FakeConnUpdate()
    updates = [(1, {"a": 1}), (2, {"b": 2}), (3, {"c": 3})]

    prune.apply_updates(conn, updates)

    assert [len(c) // 2 for c in conn.calls] == [2, 1]
    assert conn.commits == 2
    first_chunk_row_id, first_chunk_payload = conn.calls[0][0], conn.calls[0][1]
    assert first_chunk_row_id == 1
    assert first_chunk_payload.obj == {"a": 1}


def test_apply_updates_empty_list_makes_no_calls():
    conn = FakeConnUpdate()

    prune.apply_updates(conn, [])

    assert conn.calls == []
    assert conn.commits == 0


# --- actual_size ---


class FakeCursorSize:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.params = params

    def fetchone(self):
        return (self.conn.total,)


class FakeConnSize:
    def __init__(self, total):
        self.total = total
        self.params = None
        self.query_ran = False

    def cursor(self):
        self.query_ran = True
        return FakeCursorSize(self)


def test_actual_size_returns_zero_without_querying_for_empty_ids():
    conn = FakeConnSize(total=999)

    assert prune.actual_size(conn, []) == 0
    assert conn.query_ran is False


def test_actual_size_queries_with_given_ids():
    conn = FakeConnSize(total=12345)

    result = prune.actual_size(conn, [1, 2, 3])

    assert result == 12345
    assert conn.params == ([1, 2, 3],)


# --- run(): orchestration (dry-run vs real, already-pruned accounting) ---


def make_candidate(row_id, content="<p>desc</p>", title="A"):
    return {
        "id": row_id,
        "source": "greenhouse",
        "payload": {"jobs": [{"id": row_id, "title": title, "content": content}]},
        "size_before": 999,
    }


def test_run_dry_run_never_calls_apply_updates(monkeypatch):
    monkeypatch.setattr(prune.db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(prune.db, "init_schema", lambda conn: None)

    candidates = [make_candidate(1), make_candidate(2, content=None)]  # second already pruned
    monkeypatch.setattr(prune, "find_candidates", lambda conn, *, cutoff: candidates)

    apply_calls = []
    monkeypatch.setattr(prune, "apply_updates", lambda conn, updates: apply_calls.append(updates))

    summary = prune.run(days=30, dry_run=True)

    assert summary["candidates"] == 2
    assert summary["already_pruned"] == 1
    assert summary["to_prune"] == 1
    assert summary["dry_run"] is True
    assert apply_calls == []
    assert summary["estimated_bytes_reclaimed"] > 0


def test_run_real_run_applies_updates_and_reports_actual_sizes(monkeypatch):
    monkeypatch.setattr(prune.db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(prune.db, "init_schema", lambda conn: None)

    candidates = [make_candidate(1)]
    monkeypatch.setattr(prune, "find_candidates", lambda conn, *, cutoff: candidates)

    applied = []
    monkeypatch.setattr(prune, "apply_updates", lambda conn, updates: applied.append(updates))

    size_calls = []

    def fake_actual_size(conn, ids):
        size_calls.append(list(ids))
        return 1000 if len(size_calls) == 1 else 200

    monkeypatch.setattr(prune, "actual_size", fake_actual_size)

    summary = prune.run(days=30, dry_run=False)

    assert len(applied) == 1
    row_id, payload = applied[0][0]
    assert row_id == 1
    assert payload["jobs"][0]["content"] is None
    assert summary["actual_bytes_before"] == 1000
    assert summary["actual_bytes_after"] == 200
    assert summary["actual_bytes_reclaimed"] == 800
    assert size_calls == [[1], [1]]


def test_run_with_nothing_to_prune_does_not_call_actual_size_query_uselessly(monkeypatch):
    monkeypatch.setattr(prune.db, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(prune.db, "init_schema", lambda conn: None)
    monkeypatch.setattr(prune, "find_candidates", lambda conn, *, cutoff: [])

    size_calls = []
    monkeypatch.setattr(prune, "actual_size", lambda conn, ids: size_calls.append(ids) or 0)
    applied = []
    monkeypatch.setattr(prune, "apply_updates", lambda conn, updates: applied.append(updates))

    summary = prune.run(days=30, dry_run=False)

    assert summary["to_prune"] == 0
    assert applied == [[]]
    assert size_calls == [[], []]
