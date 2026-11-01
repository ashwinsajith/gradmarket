"""Production entrypoint for the web service: `python -m gradmarket.web`.

Exists instead of a bare `uvicorn ... --port $PORT` start command because
Railway runs a Dockerfile service's custom start command in exec form —
no shell, so `$PORT` would reach uvicorn as the literal string "$PORT".
Reading PORT here needs no shell wrapper, and pins the settings that must
not drift:

- one worker: the snapshot cache (store.SnapshotCache) is per-process, so
  N workers would mean N caches and N sets of Postgres queries;
- no reload, no debug: FastAPI's docs routes are already off in app.py;
- proxy headers trusted from any address: Railway terminates TLS at its
  edge and forwards plain HTTP, so without X-Forwarded-Proto the app sees
  every request as http. "*" is safe here only because the container is
  reachable solely through Railway's proxy — there is no direct path for a
  client to spoof the header.

For local development, `uvicorn gradmarket.web.app:app --reload` is still
the convenient form.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import uvicorn

DEFAULT_PORT = 8000


def server_options(env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "host": "0.0.0.0",
        "port": int(env.get("PORT", DEFAULT_PORT)),
        "workers": 1,
        "reload": False,
        "proxy_headers": True,
        "forwarded_allow_ips": "*",
        "log_level": "info",
    }


def main() -> None:
    uvicorn.run("gradmarket.web.app:app", **server_options(os.environ))


if __name__ == "__main__":
    main()
