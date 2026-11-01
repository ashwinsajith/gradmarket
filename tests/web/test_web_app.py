from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from web_seed import COLLECTION_START, DESCRIPTION_SENTINEL, NOW, seed

from gradmarket.web.app import create_app
from gradmarket.web.queries import PUBLIC_COLUMNS, Role, Stats, derive_role
from gradmarket.web.store import Snapshot, SnapshotCache, load_snapshot, make_snapshot

DERIVED_FIELDS = {
    "days_since_first_seen",
    "days_open",
    "is_new",
    "is_long_running",
    "age_is_floor",
    "age_label",
    "freshness_group",
    "cities",
}


def _role(external_id: str, *, first_seen_at, location: str = "London") -> Role:
    return derive_role(
        {
            "title": f"Role {external_id}",
            "company": "acme",
            "location": location,
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


def _snapshot(roles: list[Role], *, arrival_counts: dict | None = None) -> Snapshot:
    return make_snapshot(
        roles=roles,
        stats=Stats(postings_collected=10, collecting_since=COLLECTION_START),
        boards_monitored=195,
        arrival_counts=arrival_counts or {},
        today=NOW.date(),
    )


def _fake_snapshot() -> Snapshot:
    return _snapshot(
        [_role("new", first_seen_at=NOW - timedelta(days=2)), _role("floor", first_seen_at=COLLECTION_START)]
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


MULTI_LOCATION = "United Kingdom, UK - Hertfordshire - Ware; UK - Angus - Montrose; UK - Hertfordshire - Stevenage"


def test_page_shows_short_location_with_full_list_in_title_and_accessible_text():
    snapshot = _snapshot([_role("multi", first_seen_at=NOW - timedelta(days=2), location=MULTI_LOCATION)])
    with _client(lambda: snapshot) as client:
        html = client.get("/").text
        api_location = client.get("/api/roles").json()["roles"][0]["location"]

    full = "Hertfordshire, Ware; Angus, Montrose; Hertfordshire, Stevenage"
    assert ">Hertfordshire, Ware <span class=\"role__more\">+2 more</span>" in html
    assert f'title="{full}"' in html
    assert f'<span class="visually-hidden">{full}</span>' in html
    assert api_location == MULTI_LOCATION  # display-only: the API keeps the raw value


def test_page_has_no_absolute_http_asset_urls():
    """Regression: url_for() rendered http://testserver/static/... — behind
    Railway's https proxy that's mixed content, and the page went unstyled.
    TestClient serves plain http, exactly the scheme the app wrongly saw in
    production, so a url_for() reintroduced anywhere fails here."""
    with _client(_fake_snapshot) as client:
        html = client.get("/").text

    assert re.findall(r'(?:src|href)="http://[^"]*"', html) == []
    assert 'href="/static/site.css"' in html
    assert 'src="/static/site.js"' in html


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
    assert {"/", "/api/roles", "/api/arrivals", "/healthz"} <= set(routes)

    for path in routes:
        resp = db_client.get(path)
        assert resp.status_code == 200, path
        assert DESCRIPTION_SENTINEL not in resp.text, path


# --- hero ---


def _page(snapshot: Snapshot | None = None) -> str:
    with _client(lambda: snapshot or _fake_snapshot()) as client:
        return client.get("/").text


def test_hero_leads_with_name_then_tagline_then_subhead_then_stats():
    html = _page()

    order = [
        html.index(">GradMarket.</h1>"),
        html.index("Open UK graduate roles, watched daily."),
        html.index("company job boards, checked every morning"),
        html.index("Boards monitored"),
    ]
    assert order == sorted(order)


def test_subhead_uses_live_board_count_and_start_month():
    html = _page()

    assert "195 company job boards, checked every morning since August." in html


def test_collecting_since_shows_the_full_date_under_a_plain_label():
    html = _page()

    assert '<time datetime="2026-08-10">10 Aug 2026</time>' in html
    assert ">Collecting since</dt>" in html


def test_nav_wordmark_is_in_the_markup_for_no_js_and_hidden_by_script_only():
    html = _page()

    assert 'class="nav__brand"' in html
    assert "nav--brand-hidden" not in html  # site.js adds it; without JS the wordmark shows


# --- arrivals ---


ARRIVAL_COUNTS = {date(2026, 8, 12): 2, date(2026, 9, 3): 14, date(2026, 10, 30): 6}


def test_api_arrivals_shape():
    with _client(lambda: _snapshot([], arrival_counts=ARRIVAL_COUNTS)) as client:
        resp = client.get("/api/arrivals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == "2026-08-10"
    assert body["end"] == NOW.date().isoformat()
    assert body["peak"] == {"date": "2026-09-03", "count": 14}
    assert len(body["days"]) == (NOW.date() - date(2026, 8, 10)).days + 1


def test_api_arrivals_503_before_first_load():
    with _client(_failing_loader) as client:
        assert client.get("/api/arrivals").status_code == 503


def test_chart_caption_says_floors_are_not_counted():
    html = _page(_snapshot([], arrival_counts=ARRIVAL_COUNTS))

    assert "Roles already live when their board was first observed aren't counted — only genuine arrivals." in html
    assert "UK early-careers postings first seen, per day" in html  # title kept
    assert "artefact" not in html.lower()
    assert "Outlined" not in html


def test_chart_shows_the_peak_once():
    html = _page(_snapshot([], arrival_counts=ARRIVAL_COUNTS))

    assert html.count("Peak 14 · 3 Sep") == 1


def test_chart_has_an_accessible_data_table():
    html = _page(_snapshot([], arrival_counts=ARRIVAL_COUNTS))

    assert "<td>12 Aug 2026</td><td>2</td>" in html
    assert "<td>3 Sep 2026</td><td>14</td>" in html


# --- freshness groups ---


def _grouped_snapshot() -> Snapshot:
    return _snapshot(
        [
            _role("w1", first_seen_at=NOW - timedelta(days=1)),
            _role("w2", first_seen_at=NOW - timedelta(days=7)),
            _role("e1", first_seen_at=NOW - timedelta(days=45)),
        ]
    )


def test_groups_render_in_order_with_counts():
    html = _page(_grouped_snapshot())

    assert html.index('data-group="this_week"') < html.index('data-group="earlier"')
    assert re.search(r"This week</span>\s*<span[^>]*>0–7 days</span>\s*<span[^>]*>2 roles</span>", html)
    assert re.search(r"Earlier</span>\s*<span[^>]*>31\+ days</span>\s*<span[^>]*>1 role</span>", html)


def test_empty_group_is_omitted_not_shown_as_zero():
    html = _page(_grouped_snapshot())

    assert 'tbody class="group" data-group="this_month"' not in html
    assert "This month" not in html


def test_rows_stay_in_server_order_within_a_group():
    html = _page(_grouped_snapshot())

    assert html.index("Role w1") < html.index("Role w2")


# --- city filter ---


def test_city_options_ordered_by_count_with_counts():
    snapshot = _snapshot(
        [
            _role("a", first_seen_at=NOW, location="Edinburgh"),
            _role("b", first_seen_at=NOW, location="London"),
            _role("c", first_seen_at=NOW, location="London; Leeds"),
            _role("d", first_seen_at=NOW, location="United Kingdom, Avonmouth Customer Delivery Hub"),
        ]
    )
    html = _page(snapshot)

    options = re.findall(r'<option value="([^"]*)">([^<]*)</option>', html.split('id="f-city"')[1].split("</select>")[0])
    assert options == [
        ("", "All cities"),
        ("London", "London (2)"),
        ("Edinburgh", "Edinburgh (1)"),
        ("Leeds", "Leeds (1)"),
        ("Other", "Other (1)"),
    ]
    assert 'data-cities="London|Leeds"' in html


def test_city_filter_sits_with_company_and_source():
    html = _page()

    assert html.index('id="f-city"') < html.index('id="f-company"') < html.index('id="f-source"')

