from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web_seed import COLLECTION_START, DESCRIPTION_SENTINEL, NOW, seed

from gradmarket.web.app import ICON_FILES, ICON_SET_SHA256, ICON_VERSION, create_app
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
    html = _page().split("<body", 1)[1]  # the meta description repeats the subhead's words

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


def test_nav_logo_mark_is_always_shown_and_links_home():
    html = _page()
    home = re.search(r'<a class="nav__home"[^>]*>.*?</a>', html, re.DOTALL).group(0)

    assert 'href="/"' in home
    assert 'aria-label="GradMarket — back to top"' in home
    assert '<img class="nav__mark" src="/static/mark.svg?v=2" width="26" height="26" alt="">' in home
    # Both the mark and the wordmark are inside the one link.
    assert '<span class="nav__brand" aria-hidden="true">GradMarket</span>' in home


def test_nav_wordmark_is_always_visible_with_no_script_or_style_hiding_it():
    """The wordmark used to fade in once the hero scrolled away. It's static
    now: no class toggles it, no rule hides it, no observer watches the hero."""
    html = _page()
    css = (STATIC / "site.css").read_text()
    js = (STATIC / "site.js").read_text()

    assert "brand-hidden" not in html + css + js
    assert "data-hero" not in html and "data-hero" not in js
    brand_rule = css.split(".nav__brand {", 1)[1].split("}", 1)[0]
    assert "opacity" not in brand_rule and "visibility" not in brand_rule


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


# --- filter count vs chart (regression) ---


def test_only_the_filter_bar_carries_data_count():
    """Regression: chart bars carried data-count too, so site.js's lookup for
    the filter count matched the first bar and wrote "257 roles" into the
    chart. The count must be the only data-count, and inside the form."""
    html = _page(_snapshot([_role("a", first_seen_at=NOW)], arrival_counts={date(2026, 9, 3): 4}))

    assert html.count("data-count") == 1
    form = html.split('data-filters>', 1)[1].split("</form>", 1)[0]
    assert "data-count" in form


def test_chart_markup_has_no_attribute_the_list_uses():
    html = _page(_snapshot([], arrival_counts={date(2026, 9, 3): 4}))
    chart = html.split('class="arrivals__bars"', 1)[1].split("</div>", 1)[0]

    for attribute in ("data-count", "data-label", "data-group", "data-cities"):
        assert attribute not in chart


def test_days_with_arrivals_are_marked_for_a_minimum_height_and_empty_days_are_not():
    html = _page(_snapshot([], arrival_counts={date(2026, 9, 3): 1}))
    bars = re.findall(r'<span class="(bar[^"]*)"[^>]*data-day="([^"]*)"', html)

    some = {day for classes, day in bars if "bar--some" in classes}
    assert some == {"3 Sep 2026"}


# --- icons and metadata ---


ICONS = {"icon-16.png": 16, "icon-32.png": 32, "icon-180.png": 180, "icon-512.png": 512}
TILE = (0x0B, 0x0C, 0x0F)
UNLIT_CELL = (0x2A, 0x2E, 0x34)
STATIC = Path(__file__).resolve().parents[2] / "src" / "gradmarket" / "web" / "static"


def test_icon_links_and_metadata():
    html = _page()

    assert '<link rel="icon" type="image/png" sizes="16x16" href="/static/icon-16.png?v=2">' in html
    assert '<link rel="icon" type="image/png" sizes="32x32" href="/static/icon-32.png?v=2">' in html
    assert '<link rel="apple-touch-icon" href="/static/icon-180.png?v=2">' in html
    assert '<meta name="theme-color" content="#0b0c0f">' in html
    assert "<title>GradMarket — Open UK graduate roles</title>" in html
    assert re.search(r'<meta name="description" content="[^"]*195 company job boards', html)
    for prop in ("og:title", "og:description", "og:url", "og:type", "og:image"):
        assert f'property="{prop}"' in html, prop
    assert 'property="og:image" content="http://testserver/static/icon-512.png?v=2"' in html


def test_icon_files_are_served_as_png_at_their_stated_size():
    with _client(_fake_snapshot) as client:
        for name, size in ICONS.items():
            resp = client.get(f"/static/{name}")
            assert resp.status_code == 200, name
            assert resp.headers["content-type"] == "image/png"
            # PNG IHDR: width and height are big-endian at bytes 16-24.
            assert int.from_bytes(resp.content[16:20], "big") == size, name
            assert int.from_bytes(resp.content[20:24], "big") == size, name


def _near(rgb: tuple[int, ...], target: tuple[int, int, int], tolerance: int = 3) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(rgb, target))


def test_every_icon_is_the_dot_matrix_mark():
    """One mark at every size: each icon shows the dot-matrix grid's unlit
    cells — including 16 and 32, which used to be a solid letterform."""
    image = pytest.importorskip("PIL.Image")
    for name in ICONS:
        colours = image.open(STATIC / name).convert("RGB").getcolors(maxcolors=1 << 20)
        assert any(_near(rgb, UNLIT_CELL, tolerance=8) for _, rgb in colours), name


def test_every_icon_has_non_background_pixels_in_its_middle_third():
    """The mark is actually drawn, and drawn in the middle — not an empty
    tile, and not shifted off-centre."""
    image = pytest.importorskip("PIL.Image")
    for name, size in ICONS.items():
        icon = image.open(STATIC / name).convert("RGB")
        third = size // 3
        middle = icon.crop((third, third, size - third, size - third))
        colours = middle.getcolors(maxcolors=1 << 20)
        assert any(not _near(rgb, TILE, tolerance=6) for _, rgb in colours), name


def test_chart_month_labels_are_three_letter_abbreviations():
    html = _page(_snapshot([], arrival_counts={date(2026, 9, 3): 4}))
    months = html.split('class="arrivals__months', 1)[1].split("</div>", 1)[0]

    labels = re.findall(r">([A-Z]{3})</span>", months)
    assert labels == ["AUG", "SEP", "OCT", "NOV"]
    assert 'class="arrivals__month--end" style="flex-grow: 1">NOV<' in months


def test_nav_mark_svg_is_the_dot_matrix_g():
    """mark.svg comes from make_icons.py's DOT_G grid: a tile plus 5x7 cells —
    16 lit, 1 accent (the crossbar's end), 18 unlit."""
    with _client(_fake_snapshot) as client:
        resp = client.get("/static/mark.svg")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    fills = re.findall(r'fill="(#[0-9a-f]{6})"', resp.text)
    assert fills[0] == "#0b0c0f"  # the tile
    cells = fills[1:]
    assert len(cells) == 35
    assert (cells.count("#f2f3f5"), cells.count("#d99a2b"), cells.count("#2a2e34")) == (16, 1, 18)


# --- icon cache-busting ---


def test_every_icon_url_carries_the_version():
    html = _page()
    icon_urls = re.findall(r'(?:href|src|content)="[^"]*?/static/((?:icon-\d+\.png|mark\.svg)[^"]*)"', html)

    assert len(icon_urls) == 5
    assert all(url.endswith(f"?v={ICON_VERSION}") for url in icon_urls), icon_urls


def test_icon_version_was_bumped_when_the_icons_changed():
    """Fails when the icon files no longer match the hash recorded beside
    ICON_VERSION: bump ICON_VERSION, then set ICON_SET_SHA256 to the hash
    this test prints."""
    digest = hashlib.sha256()
    for name in ICON_FILES:
        digest.update(name.encode())
        digest.update((STATIC / name).read_bytes())

    assert digest.hexdigest() == ICON_SET_SHA256, (
        f"icons changed: bump ICON_VERSION in gradmarket/web/app.py and set ICON_SET_SHA256 = {digest.hexdigest()!r}"
    )


def test_versioned_icon_url_still_serves_the_file():
    with _client(_fake_snapshot) as client:
        resp = client.get(f"/static/icon-16.png?v={ICON_VERSION}")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"

