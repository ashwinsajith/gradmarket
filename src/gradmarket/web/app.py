"""FastAPI app for the public frontend.

Run locally (DATABASE_URL from .env, same as every other entry point):

    uvicorn gradmarket.web.app:app --reload

create_app() takes the cache as a parameter so tests can hand it one backed
by a rolled-back test connection or a fake loader; only the module-level
`app` builds a real pool, and only on startup, never at import — importing
this module in a test must not open a connection.

FastAPI's /docs, /redoc and /openapi.json are switched off: every route
here is part of the public surface, and nothing public should exist that
wasn't put there deliberately.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gradmarket.web.display import display_location
from gradmarket.web.queries import Role
from gradmarket.web.store import Snapshot, SnapshotCache, make_pool, pooled_loader

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent
REPO_URL = "https://github.com/ashwinsajith/gradmarket"


def _day_month(d: datetime) -> str:
    return f"{d.day} {d:%b}"


def _full_date(d: datetime) -> str:
    return f"{d.day} {d:%b %Y}"


# The list's freshness groups, in display order: (key, heading, range).
# Keys match queries.freshness_group().
FRESHNESS_GROUPS = [
    ("this_week", "This week", "0–7 days"),
    ("this_month", "This month", "8–30 days"),
    ("earlier", "Earlier", "31+ days"),
]


def group_roles(roles: list[Role]) -> list[dict[str, Any]]:
    """Roles split by freshness group, server order kept within each. An
    empty group is left out entirely, never shown as a zero."""
    groups = []
    for key, heading, span in FRESHNESS_GROUPS:
        members = [r for r in roles if r.freshness_group == key]
        if members:
            groups.append({"key": key, "heading": heading, "span": span, "roles": members})
    return groups


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["day_month"] = _day_month
templates.env.filters["full_date"] = _full_date
templates.env.filters["location_display"] = display_location


def create_app(cache: SnapshotCache | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if cache is not None:
            app.state.cache = cache
            yield
            return
        load_dotenv()
        pool = make_pool(os.environ["DATABASE_URL"])
        # wait=False: the service comes up (and /healthz answers 503) even
        # while Postgres is unreachable, instead of failing to boot.
        pool.open(wait=False)
        app.state.cache = SnapshotCache(pooled_loader(pool))
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="GradMarket", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    def snapshot_or_503() -> Snapshot | JSONResponse:
        # get() only raises before the first successful load — after that it
        # serves the stale snapshot, and /healthz's cache age says how stale.
        try:
            return app.state.cache.get()
        except Exception:
            log.exception("no snapshot available")
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        snapshot = snapshot_or_503()
        if isinstance(snapshot, JSONResponse):
            return PlainTextResponse("GradMarket is temporarily unavailable. Try again shortly.", status_code=503)
        roles = snapshot.roles
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "roles": roles,
                "groups": group_roles(roles),
                "arrivals": snapshot.arrivals,
                "city_counts": snapshot.city_counts,
                "stats": snapshot.stats,
                "boards_monitored": snapshot.boards_monitored,
                "companies": sorted({r.company for r in roles}),
                "sources": sorted({r.source for r in roles}),
                # The most recent observation of any shown role: when the
                # pipeline last saw these boards, not when this page cached.
                "last_checked": max((r.last_seen_at for r in roles), default=None),
                "repo_url": REPO_URL,
                "site_url": str(request.base_url).rstrip("/"),
            },
        )

    @app.get("/api/roles")
    def api_roles() -> JSONResponse:
        snapshot = snapshot_or_503()
        if isinstance(snapshot, JSONResponse):
            return snapshot
        return JSONResponse({"count": len(snapshot.roles), "roles": [r.to_json() for r in snapshot.roles]})

    @app.get("/api/arrivals")
    def api_arrivals() -> JSONResponse:
        snapshot = snapshot_or_503()
        if isinstance(snapshot, JSONResponse):
            return snapshot
        return JSONResponse(snapshot.arrivals.to_json())

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        snapshot = snapshot_or_503()
        if isinstance(snapshot, JSONResponse):
            return snapshot
        return JSONResponse(
            {"status": "ok", "row_count": len(snapshot.roles), "cache_age_seconds": round(app.state.cache.age(), 1)}
        )

    return app


app = create_app()
