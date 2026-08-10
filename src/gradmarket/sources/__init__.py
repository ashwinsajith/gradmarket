"""Registry of ingest sources.

ingest.py dispatches through SOURCES and never imports a specific source
module, so sources stay interchangeable.
"""

from __future__ import annotations

from gradmarket.sources import greenhouse

SOURCES = {
    "greenhouse": greenhouse,
}
