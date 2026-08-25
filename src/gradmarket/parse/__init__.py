"""Registry of parse extractors, mirroring gradmarket.sources.SOURCES.

parse_run.py dispatches through EXTRACTORS and never imports a specific
extractor module, so sources stay interchangeable at the parsing layer too.
"""

from __future__ import annotations

from gradmarket.parse import ashby, greenhouse, lever, workable

EXTRACTORS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
}
