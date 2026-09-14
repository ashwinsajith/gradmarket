"""Shared description-stripping logic for raw_fetches payloads.

Lives in the installed package (not scripts/) because two things need the
exact same field-name knowledge and must never drift apart: scripts/
prune_raw_fetches.py (which does the stripping) and parse_run.py's --full
guard (which detects whether stripping has already happened, to refuse a
destructive rebuild — see db.count_stripped_raw_fetches). A field added here
without updating both call sites would silently break one of them.

Field names differ by source. Both the field the parse layer reads AND its
duplicate plain-text sibling are stripped where one exists — Ashby's
descriptionHtml/descriptionPlain and Lever's description/descriptionPlain
each carry the same content twice. Workday isn't here: its raw_fetches
payload (the list endpoint) has no description field at all — that lives in
raw_details, fetched separately by detail_run.py.
"""

from __future__ import annotations

from typing import Any

DESCRIPTION_FIELDS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("content",),
    "ashby": ("descriptionHtml", "descriptionPlain"),
    "lever": ("description", "descriptionPlain"),
    "workable": ("description",),
}


def _jobs_list(payload: Any, source: str) -> list[Any] | None:
    """Lever's raw_fetches payload is a bare array; every other handled
    source is a {"jobs": [...]} object (see CLAUDE.md's data model notes)."""
    if source == "lever":
        return payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        return payload.get("jobs")
    return None


def strip_descriptions(payload: Any, source: str) -> tuple[Any, bool]:
    """Returns (new_payload, changed). Never mutates `payload` in place —
    every job dict and the top-level payload are shallow-copied before any
    field is reassigned, so the caller's original object is untouched.
    changed is False when this source has no known description field
    (Workday, or an unrecognised source) or when every job's field(s) are
    already empty/None (already pruned, or never had a description)."""
    fields = DESCRIPTION_FIELDS.get(source)
    if not fields:
        return payload, False

    jobs = _jobs_list(payload, source)
    if not jobs:
        return payload, False

    changed = False
    new_jobs = []
    for job in jobs:
        if not isinstance(job, dict):
            new_jobs.append(job)
            continue
        new_job = dict(job)
        for field in fields:
            if new_job.get(field):
                new_job[field] = None
                changed = True
        new_jobs.append(new_job)

    if not changed:
        return payload, False

    if source == "lever":
        return new_jobs, True
    new_payload = dict(payload)
    new_payload["jobs"] = new_jobs
    return new_payload, True
