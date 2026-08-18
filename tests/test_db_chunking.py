"""Chunking behavior for db.py's bulk_* functions.

This tests our own control flow (call count, batch boundaries, result
merging across chunks) with a minimal fake cursor — NOT SQL validity against
real Postgres, which is scripts/smoke_parse.py's job.
"""

from __future__ import annotations

from types import SimpleNamespace

from gradmarket import db


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_params = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.conn.calls.append(list(params))
        self._last_params = params
        self.rowcount = len(params) // self.conn.row_width

    def fetchall(self):
        n = len(self._last_params) // self.conn.row_width
        rows = []
        for i in range(n):
            external_id = self._last_params[i * self.conn.row_width + self.conn.external_id_index]
            rows.append((external_id, self.conn.next_id))
            self.conn.next_id += 1
        return rows


class FakeConn:
    def __init__(self, row_width, external_id_index=0):
        self.calls: list[list] = []
        self.commits = 0
        self.row_width = row_width
        self.external_id_index = external_id_index
        self.next_id = 1

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


def make_postings(n):
    return [
        SimpleNamespace(
            external_id=f"p{i}",
            title=f"Title {i}",
            location="London",
            department=None,
            url=None,
            content_hash=f"hash{i}",
        )
        for i in range(n)
    ]


def test_bulk_upsert_postings_chunks_at_1000_rows():
    conn = FakeConn(row_width=10, external_id_index=2)
    postings = make_postings(2500)

    result = db.bulk_upsert_postings(
        conn, source="greenhouse", company="acme", postings=postings, observed_at="t0"
    )

    assert [len(c) // 10 for c in conn.calls] == [1000, 1000, 500]
    assert conn.commits == 3
    assert len(result) == 2500
    assert result["p0"] is not None
    assert result["p2499"] is not None
    assert len(set(result.values())) == 2500  # every posting got a distinct id


def test_bulk_upsert_postings_handles_exact_multiple_of_chunk_size():
    conn = FakeConn(row_width=10, external_id_index=2)
    postings = make_postings(2000)

    result = db.bulk_upsert_postings(
        conn, source="greenhouse", company="acme", postings=postings, observed_at="t0"
    )

    assert [len(c) // 10 for c in conn.calls] == [1000, 1000]
    assert len(result) == 2000


def test_bulk_upsert_postings_empty_list_makes_no_calls():
    conn = FakeConn(row_width=10, external_id_index=2)

    result = db.bulk_upsert_postings(conn, source="greenhouse", company="acme", postings=[], observed_at="t0")

    assert result == {}
    assert conn.calls == []
    assert conn.commits == 0


def test_bulk_append_posting_versions_chunks_at_1000_rows():
    conn = FakeConn(row_width=6)
    versions = [
        {
            "posting_id": i,
            "observed_at": "t0",
            "content_hash": f"hash{i}",
            "title": f"Title {i}",
            "location": "London",
            "description_raw": "desc",
        }
        for i in range(2500)
    ]

    db.bulk_append_posting_versions(conn, versions=versions)

    assert [len(c) // 6 for c in conn.calls] == [1000, 1000, 500]
    assert conn.commits == 3


def test_bulk_append_posting_versions_empty_list_makes_no_calls():
    conn = FakeConn(row_width=6)

    db.bulk_append_posting_versions(conn, versions=[])

    assert conn.calls == []
    assert conn.commits == 0


def test_bulk_update_classifications_chunks_at_1000_rows():
    conn = FakeConn(row_width=4)
    classifications = [
        {"id": i, "location_class": "uk", "seniority_class": "early", "classified_at": "t0"}
        for i in range(2500)
    ]

    total = db.bulk_update_classifications(conn, classifications=classifications)

    assert [len(c) // 4 for c in conn.calls] == [1000, 1000, 500]
    assert conn.commits == 3
    assert total == 2500


def test_bulk_update_classifications_empty_list_makes_no_calls():
    conn = FakeConn(row_width=4)

    total = db.bulk_update_classifications(conn, classifications=[])

    assert total == 0
    assert conn.calls == []
    assert conn.commits == 0


def test_bulk_update_classifications_respects_commit_false():
    conn = FakeConn(row_width=4)
    classifications = [{"id": 1, "location_class": "uk", "seniority_class": "early", "classified_at": "t0"}]

    db.bulk_update_classifications(conn, classifications=classifications, commit=False)

    assert conn.commits == 0
