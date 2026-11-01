"""Behaviour that only exists in a real browser: layout and scroll position.

These drive headless Chrome against the real app, served on a local port
with an in-memory snapshot — no database, no external HTTP. They skip when
no Chrome/Chromium binary is found (set CHROME_BIN to point at one).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from web_seed import COLLECTION_START, NOW

from gradmarket.web.app import create_app
from gradmarket.web.queries import Stats, derive_role
from gradmarket.web.store import SnapshotCache, make_snapshot

CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]


def _chrome() -> str:
    for path in CHROME_CANDIDATES:
        if path and Path(path).exists():
            return path
    pytest.skip("no Chrome/Chromium binary found (set CHROME_BIN)")


def _snapshot():
    """150 London roles and 4 in Edinburgh: filtering to Edinburgh shrinks
    the page by far more than a viewport."""
    roles = []
    for i in range(154):
        roles.append(
            derive_role(
                {
                    "title": f"Graduate role {i}",
                    "company": "acme",
                    "location": "Edinburgh" if i % 40 == 0 else "London",
                    "location_class": "uk",
                    "source": "greenhouse",
                    "first_seen_at": NOW - timedelta(days=i % 60, hours=1),
                    "last_seen_at": NOW,
                    "is_open": True,
                    "url": f"https://example.test/{i}",
                    "board_started_at": COLLECTION_START,
                },
                now=NOW,
            )
        )
    return make_snapshot(
        roles=roles,
        stats=Stats(postings_collected=1000, collecting_since=COLLECTION_START),
        boards_monitored=195,
        arrival_counts={},
        today=NOW.date(),
    )


# Runs inside the page (same origin, via an iframe): scroll so the viewport's
# bottom sits near the end of the unfiltered list, apply a filter, then
# report where things ended up.
HARNESS = """<!doctype html><html><body style="margin:0"><pre id="out">pending</pre>
<iframe id="f" src="/" width="1200" height="800" style="border:0"></iframe>
<script>
document.getElementById("f").addEventListener("load", () => setTimeout(() => {
  const d = document.getElementById("f").contentDocument, w = d.defaultView;
  const table = d.querySelector("table.roles");
  const sticky = d.querySelector(".nav").offsetHeight + d.querySelector("[data-filters]").offsetHeight;
  function run(field, value) {
    const el = d.getElementById(field);
    el.value = ""; el.dispatchEvent(new w.Event("input", {bubbles: true}));
    const tableBottom = table.getBoundingClientRect().bottom + w.scrollY;
    w.scrollTo(0, tableBottom - w.innerHeight + 40);
    const before = w.scrollY;
    // The row the reader is looking at: whatever's mid-viewport.
    const watched = d.elementFromPoint(400, w.innerHeight / 2).closest("tr.role");
    const watchedTopBefore = watched ? watched.getBoundingClientRect().top : null;
    el.value = value; el.dispatchEvent(new w.Event("input", {bubbles: true}));
    const rect = table.getBoundingClientRect();
    return {before: before, after: w.scrollY, tableTop: rect.top, tableBottom: rect.bottom,
            watchedStillShown: watched ? !watched.hidden : null,
            watchedTopBefore: watchedTopBefore,
            watchedTopAfter: watched ? watched.getBoundingClientRect().top : null,
            sticky: sticky, viewport: w.innerHeight,
            footerTop: d.querySelector("footer").getBoundingClientRect().top};
  }
  // stillTall first, on the freshly loaded page, so it doesn't inherit the
  // scroll history of the shrinking case's jump.
  const stillTall = run("f-city", "London");
  const shrinks = run("f-city", "Edinburgh");
  const out = {stillTall: stillTall, shrinks: shrinks};
  document.getElementById("out").textContent = JSON.stringify(out);
}, 300));
</script></body></html>"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(tmp_path) -> Iterator[str]:
    (tmp_path / "harness.html").write_text(HARNESS)
    app = create_app(cache=SnapshotCache(_snapshot))
    app.mount("/__test", StaticFiles(directory=tmp_path), name="harness")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("test server didn't start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _run_harness(base_url: str) -> dict:
    chrome = _chrome()
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--virtual-time-budget=5000", "--window-size=1300,1000",
         "--dump-dom", f"{base_url}/__test/harness.html"],
        capture_output=True, text=True, timeout=60, check=False,  # judged by its output, not exit code
    )
    match = re.search(r'<pre id="out">(.*?)</pre>', result.stdout, re.DOTALL)
    assert match and match.group(1) != "pending", f"harness didn't report: {result.stdout[-500:]}"
    return json.loads(match.group(1).replace("&quot;", '"'))


def test_filtering_to_a_small_set_from_the_bottom_keeps_the_list_in_view(served):
    out = _run_harness(served)["shrinks"]

    # The list section is not above the viewport, and the reader wasn't
    # dropped into the footer.
    assert out["tableBottom"] > out["sticky"], out
    assert out["tableTop"] < out["viewport"], out
    assert out["footerTop"] > out["sticky"], out
    # It sits just under the sticky nav and filter bar.
    assert abs(out["tableTop"] - out["sticky"]) <= 2, out


def test_filtering_that_leaves_the_page_tall_enough_does_not_move_the_view(served):
    """Nothing happens when the page is still tall enough. Not "scrollY is
    unchanged": rows removed *above* the viewport shrink the page above the
    reader, and the browser's scroll anchoring shifts scrollY to hold the
    visible content still. What must hold is that the row being read stays
    put — and that the list wasn't yanked to its top."""
    out = _run_harness(served)["stillTall"]

    assert out["watchedStillShown"] is True, out
    assert abs(out["watchedTopAfter"] - out["watchedTopBefore"]) <= 1, out
    assert out["tableTop"] < 0, out  # still deep in the list, not reset to its top
