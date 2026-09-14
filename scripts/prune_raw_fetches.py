#!/usr/bin/env python3
"""Manual maintenance utility: strip description text from old raw_fetches rows.

Not part of the pipeline — run by hand when disk usage warrants it, not from
ingest.py/parse_run.py/pipeline.py. raw_fetches keeps the full untouched
payload from every daily fetch forever (see CLAUDE.md's "raw first, parse
later" rule), and the bulk of that is job description text duplicated across
every snapshot of an unchanged posting. This script rewrites payloads older
than a retention window (default 30 days) to drop description text while
preserving everything close-detection and identity actually need: job ids,
titles, locations, and the array/object structure of the payload.

Field names differ by source, defined in gradmarket.raw_fetches_pruning
(shared with parse_run.py's --full guard — see db.count_stripped_raw_fetches
— rather than duplicated here, so the two can't silently drift apart). Both
the field the parse layer reads AND its duplicate plain-text sibling are
stripped where one exists — Ashby's descriptionHtml/descriptionPlain and
Lever's description/descriptionPlain each carry the same content twice, and
leaving the unused sibling behind would defeat most of the point of running
this. Workday has no entry there: its raw_fetches payload (the list
endpoint) has no description field at all — that lives in raw_details,
fetched separately by detail_run.py — so there's nothing here to strip for
it.

A stripped payload still parses correctly through the existing extractors:
description text plays no part in identity or close-detection (external_id,
titles, locations are all untouched), only in content_hash and
posting_versions — see tests/test_prune_raw_fetches.py for a same-external-
id-set check per source.

Once any row has been pruned, parse_run.py's --full refuses to run without
--i-know-this-destroys-history: --full truncates posting_versions before
rebuilding it from raw_fetches, which would permanently destroy the only
surviving copy of description text for pruned rows (see CLAUDE.md).

Idempotent and safe to re-run: a row whose description field(s) are already
None (already pruned, or never had one) is left alone — no extra write, no
special "already pruned" marker column needed.

Loads every candidate row into memory at once; fine for an occasional manual
run at this project's scale, not built for a background/scheduled job.

Usage:
    python scripts/prune_raw_fetches.py --dry-run
    python scripts/prune_raw_fetches.py --days 30
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from gradmarket import db
from gradmarket.raw_fetches_pruning import DESCRIPTION_FIELDS, strip_descriptions

DEFAULT_RETENTION_DAYS = 30
CHUNK_SIZE = 500


def _json_size(payload: Any) -> int:
    """Approximate on-the-wire byte size. Not the same number Postgres's
    pg_column_size would report for the JSONB column (different internal
    representation), but good enough for a --dry-run estimate — the real
    run reports actual, Postgres-measured sizes instead (see actual_size)."""
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _chunked(items: list[Any], size: int) -> Any:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def find_candidates(conn: Any, *, cutoff: datetime) -> list[dict]:
    """Every row older than `cutoff` for a source this script knows how to
    strip, with payload IS NOT NULL (a failed fetch has nothing to strip).
    Workday and any unrecognised source are excluded here directly in SQL,
    not just skipped later — there's no point paging their payloads into
    memory at all."""
    sources = list(DESCRIPTION_FIELDS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source, payload, pg_column_size(payload) AS size_before
            FROM raw_fetches
            WHERE fetched_at < %s AND payload IS NOT NULL AND source = ANY(%s)
            ORDER BY id
            """,
            (cutoff, sources),
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def apply_updates(conn: Any, updates: list[tuple[int, Any]]) -> None:
    """One multi-row UPDATE ... FROM (VALUES ...) per CHUNK_SIZE rows, same
    chunking pattern as db.py's bulk_* functions, and for the same reason:
    Postgres caps a single query's parameters."""
    for chunk in _chunked(updates, CHUNK_SIZE):
        values_sql = ", ".join(["(%s, %s)"] * len(chunk))
        params: list[Any] = []
        for row_id, payload in chunk:
            params.extend([row_id, Jsonb(payload)])
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE raw_fetches AS rf
                SET payload = v.payload
                FROM (VALUES {values_sql}) AS v(id, payload)
                WHERE rf.id = v.id::bigint
                """,
                params,
            )
        conn.commit()


def actual_size(conn: Any, row_ids: list[int]) -> int:
    """Real, Postgres-measured total on-disk size of these rows' payload
    column right now — called before and after apply_updates to report
    actual bytes reclaimed, not an estimate."""
    if not row_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(pg_column_size(payload)), 0) FROM raw_fetches WHERE id = ANY(%s)",
            (row_ids,),
        )
        return cur.fetchone()[0]


def run(*, days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
    conn = db.get_connection()
    db.init_schema(conn)

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = find_candidates(conn, cutoff=cutoff)

    to_update: list[tuple[int, Any]] = []
    already_pruned = 0
    estimated_before = 0
    estimated_after = 0

    for row in rows:
        stripped, changed = strip_descriptions(row["payload"], row["source"])
        if not changed:
            already_pruned += 1
            continue
        to_update.append((row["id"], stripped))
        estimated_before += _json_size(row["payload"])
        estimated_after += _json_size(stripped)

    summary: dict[str, Any] = {
        "cutoff": cutoff,
        "candidates": len(rows),
        "already_pruned": already_pruned,
        "to_prune": len(to_update),
        "dry_run": dry_run,
    }

    if dry_run:
        summary["estimated_bytes_before"] = estimated_before
        summary["estimated_bytes_after"] = estimated_after
        summary["estimated_bytes_reclaimed"] = estimated_before - estimated_after
        conn.close()
        return summary

    row_ids = [row_id for row_id, _ in to_update]
    actual_before = actual_size(conn, row_ids)

    apply_updates(conn, to_update)

    actual_after = actual_size(conn, row_ids)
    conn.close()

    summary["actual_bytes_before"] = actual_before
    summary["actual_bytes_after"] = actual_after
    summary["actual_bytes_reclaimed"] = actual_before - actual_after
    return summary


def _human_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Strip descriptions from rows fetched more than this many days ago (default: {DEFAULT_RETENTION_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows and roughly how much space would be reclaimed, without writing anything",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run(days=args.days, dry_run=args.dry_run)

    prefix = "[dry run] " if args.dry_run else ""
    print(f"{prefix}Cutoff: rows fetched before {summary['cutoff'].isoformat()}")
    print(f"{prefix}{summary['candidates']} candidate row(s), {summary['already_pruned']} already pruned/no-op")

    if args.dry_run:
        print(
            f"{prefix}Would prune {summary['to_prune']} row(s): "
            f"~{_human_mb(summary['estimated_bytes_before'])} -> ~{_human_mb(summary['estimated_bytes_after'])} "
            f"(~{_human_mb(summary['estimated_bytes_reclaimed'])} reclaimed, estimated)"
        )
    else:
        print(
            f"Pruned {summary['to_prune']} row(s): "
            f"{_human_mb(summary['actual_bytes_before'])} -> {_human_mb(summary['actual_bytes_after'])} "
            f"({_human_mb(summary['actual_bytes_reclaimed'])} reclaimed)"
        )


if __name__ == "__main__":
    main()
