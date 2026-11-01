from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from web_seed import COLLECTION_START, DESCRIPTION_SENTINEL, NOW, seed

from gradmarket.web.app import create_app
from gradmarket.web.queries import PUBLIC_COLUMNS, Role, Stats, derive_role
from gradmarket.web.store import Snapshot, SnapshotCache, load_snapshot

DERIVED_FIELDS = {"days_since_first_seen", "days_open", "is_new", "is_long_running", "age_is_floor", "age_label"}


def _role(external_id: str, *, first_seen_at) -> Role:
    return derive_role(
        {
            "title": f"Role {external_id}",
            "company": "acme",
            "location": "London",
            "location_class": "uk",
            "source": "greenhouse",
            "first_seen_at": first_seen_at,
            "last_seen_at": NOW,
            "is_open": True,
            "url": f"https://example.test/{external_id}",
            "board_started_at": COLLECTION_START,
        },
        now=NOW,
    )


def _client(loader: Callable[[], Snapshot]) -> TestClient:
    return TestClient(create_app(cache=SnapshotCache(loader)))


def _fake_snapshot() -> Snapshot:
    return Snapshot(
        roles=[_role("new", first_seen_at=NOW - timedelta(days=2)), _role("floor", first_seen_at=COLLECTION_START)],
        stats=Stats(postings_collected=10, collecting_since=COLLECTION_START),
        boards_monitored=195,
    )


def _failing_loader() -> Snapshot:
    raise RuntimeError("db down")


def _get_routes(client: TestClient) -> list[str]:
    """Every parameterless GET route the app serves — new ones are picked up
    automatically, so the description check below covers them too."""
    return sorted(
        r.path for r in client.app.routes if "GET" in getattr(r, "methods", ()) and "{" not in r.path
    )


# --- shape and status (fake loader, no Postgres) ---


def test_api_roles_shape():
    with _client(_fake_snapshot) as client:
        resp = client.get("/api/roles")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"count", "roles"}
    assert body["count"] == 2
    for role in body["roles"]:
        assert set(role) == set(PUBLIC_COLUMNS) | DERIVED_FIELDS


def test_api_roles_floor_renders_as_n_plus_days():
    with _client(_fake_snapshot) as client:
        roles = {r["url"].rsplit("/", 1)[1]: r for r in client.get("/api/roles").json()["roles"]}

    assert roles["floor"]["age_is_floor"] is True
    assert roles["floor"]["age_label"] == "83+ days"
    assert roles["new"]["age_label"] == "2 days"


def test_healthz_reports_row_count_and_cache_age():
    with _client(_fake_snapshot) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["row_count"] == 2
    assert 0 <= body["cache_age_seconds"] < 5


def test_every_endpoint_503s_before_first_successful_load():
    with _client(_failing_loader) as client:
        for path in ("/", "/healthz", "/api/roles"):
            assert client.get(path).status_code == 503, path


def test_page_is_server_rendered_with_stats_and_rows():
    with _client(_fake_snapshot) as client:
        resp = client.get("/")

    assert resp.status_code == 200
    html = resp.text
    assert "Boards monitored" in html and ">195<" in html
    assert "Boards tracked" not in html
    assert "Role new" in html and "Role floor" in html  # rows in the first paint, no client fetch
    assert "First seen" in html and "Posted" not in html


def test_page_renders_floor_as_n_plus_days_never_exact():
    with _client(_fake_snapshot) as client:
        html = client.get("/").text

    assert "83+ days" in html
    assert "83 days" not in html
    assert "2 days" in html


def test_static_assets_are_served():
    with _client(_fake_snapshot) as client:
        assert client.get("/static/site.css").status_code == 200
        assert client.get("/static/site.js").status_code == 200


def test_auto_docs_are_not_exposed():
    with _client(_fake_snapshot) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


# --- against real SQL (rolled-back transaction; see conftest.py) ---


@pytest.fixture
def db_client(pg_conn):
    seed(pg_conn)
    with _client(lambda: load_snapshot(pg_conn, boards_monitored=195, now=NOW)) as client:
        yield client


def test_api_roles_excludes_closed_non_uk_and_non_early(db_client):
    ids = [r["url"].rsplit("/", 1)[1] for r in db_client.get("/api/roles").json()["roles"]]

    assert ids == ["new", "newco_1", "mid", "floor"]


def test_api_roles_floors_from_sql_render_as_n_plus_days(db_client):
    roles = {r["url"].rsplit("/", 1)[1]: r for r in db_client.get("/api/roles").json()["roles"]}

    assert roles["floor"]["age_label"] == "83+ days"
    assert roles["newco_1"]["age_label"] == "3+ days"
    assert roles["mid"]["age_label"] == "30 days"


def test_page_floors_from_sql_render_as_n_plus_days(db_client):
    html = db_client.get("/").text

    assert "83+ days" in html and "83 days" not in html
    assert "3+ days" in html and "3 days" not in html
    assert "30 days" in html


def test_no_response_body_contains_description_text(db_client):
    routes = _get_routes(db_client)
    assert {"/", "/api/roles", "/healthz"} <= set(routes)

    for path in routes:
        resp = db_client.get(path)
        assert resp.status_code == 200, path
        assert DESCRIPTION_SENTINEL not in resp.text, path
