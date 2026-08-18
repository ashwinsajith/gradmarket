"""Session-wide test safety net.

DATABASE_URL is forced to an unreachable placeholder before any test module
is even collected, for the whole session. Every test that needs DB behaviour
uses a FakeDB (see test_parse_run.py, test_classify_run.py, etc.) — this
exists so that if a test ever forgets to mock db/ingest/parse_run/
classify_run and falls through to a real connection, it fails immediately
and loudly instead of silently succeeding against production. That's not
hypothetical: it's exactly what happened when a test_pipeline.py test didn't
mock a newly-added classify_run.run() call and ran a real, full
classification pass against production data.

localhost on a low, essentially-never-listening port gives a fast
"connection refused" rather than a slow timeout against a black-holed
address — a hang reads as "the test suite is stuck", not "the test suite
just caught a real bug".
"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "postgresql://conftest-poison:conftest-poison@127.0.0.1:1/conftest-poison"
