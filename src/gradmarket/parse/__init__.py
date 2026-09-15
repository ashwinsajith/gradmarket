"""Registry of parse extractors, mirroring gradmarket.sources.SOURCES.

parse_run.py dispatches through EXTRACTORS and never imports a specific
extractor module, so sources stay interchangeable at the parsing layer too —
including Workday, whose two-stage extract(payload, context) differs in
shape from the other four's extract(payload); see NEEDS_DETAILS in
parse/base.py and parse_run.process_row for how that stays generic.
"""

from __future__ import annotations

from gradmarket.parse import ashby, greenhouse, lever, workable, workday

EXTRACTORS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "workday": workday,
}
