"""Healthcheck ping, shared by whichever entry point owns overall run status.

Neither ingest.py nor parse_run.py pings anything themselves — both stay
independently runnable on their own. pipeline.py is what owns this, since
it's the only place that knows whether the full run (both stages) actually
succeeded.
"""

from __future__ import annotations

import os

import requests

HEALTHCHECK_TIMEOUT = 10


def ping_healthcheck(*, failed: bool) -> None:
    base_url = os.environ.get("HEALTHCHECK_URL")
    if not base_url:
        return
    url = f"{base_url}/fail" if failed else base_url
    try:
        requests.get(url, timeout=HEALTHCHECK_TIMEOUT)
    except requests.RequestException as exc:
        print(f"healthcheck ping failed: {exc}")
