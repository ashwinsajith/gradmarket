"""Parsing layer: turn raw_fetches into normalised postings + version history.

A separate pass over raw_fetches, run independently of ingest.py. Idempotent
by default (tracks raw_fetches.parsed_at); pass --full to wipe postings and
posting_versions and rebuild them from scratch — the derived tables are
disposable, raw_fetches is the source of truth. A parser bug is recoverable;
a collection gap is not.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from gradmarket import db
from gradmarket.config import load_companies, resolve_companies_file
from gradmarket.parse import EXTRACTORS
from gradmarket.raw_fetches_pruning import strip_descriptions

# Empty feeds always trip the guard. A shrink of more than this ratio only
# trips it when the previous count was at least SHRINK_GUARD_MIN_PREVIOUS —
# below that, a real drop on a small board looks identical to a collapse, and
# guarding it would mean never detecting genuine closures on small boards.
SHRINK_GUARD_RATIO = 0.5
SHRINK_GUARD_MIN_PREVIOUS = 10


class DestructiveFullRebuildRefused(RuntimeError):
    """Raised by run() when --full would destroy already-pruned description
    history and --i-know-this-destroys-history wasn't passed."""


def _guard_tripped(current_count: int, previous_count: int | None) -> bool:
    if current_count == 0:
        return True
    if previous_count is not None and previous_count >= SHRINK_GUARD_MIN_PREVIOUS:
        return current_count < previous_count * SHRINK_GUARD_RATIO
    return False


def _deduplicate_postings(postings: list, *, source: str, company: str) -> list:
    """Keep the last occurrence of each external_id. A payload with the same
    id twice makes bulk_upsert_postings's multi-row UPSERT try to update the
    same row twice in one statement (CardinalityViolation: "ON CONFLICT DO
    UPDATE command cannot affect row a second time"). Any source could
    produce this, so this guard is general — see lever.fetch()'s own
    pagination-overlap dedup for one specific cause, already fixed upstream
    of here, but not the only possible one."""
    by_id: dict[str, Any] = {}
    for p in postings:
        if p.external_id in by_id:
            print(f"WARNING: {source}/{company}: duplicate external_id {p.external_id!r} in payload, keeping last occurrence")
        by_id[p.external_id] = p
    return list(by_id.values())


def process_row(conn: Any, row: dict, *, dry_run: bool = False) -> dict:
    """Process one raw_fetches row. Returns counts of what changed (or would
    change, under dry_run): inserted, updated, closed, versions.

    Once this row's postings/versions are upserted and it's marked parsed,
    description text is stripped from its own raw_fetches.payload (reusing
    gradmarket.raw_fetches_pruning.strip_descriptions — see CLAUDE.md) —
    posting_versions.description_raw already holds that text and appends a
    new version whenever it changes, so keeping a second full copy in
    raw_fetches once a row is parsed is pure duplication, and it's most of
    why raw_fetches grows the way it does. This happens unconditionally
    (not just for old rows the way scripts/prune_raw_fetches.py's manual
    pass does), and only for rows that reach this point:
      - it's the LAST thing this function does, after every write for this
        row has already committed (commit=commit, same as everything
        above) — never strip before a row's own parse has succeeded, and
        if anything above raises, this line is simply never reached.
      - it's skipped entirely under dry_run — nothing this function does
        under dry_run is meant to persist, and stripping would be a real,
        unconditional write with no corresponding rollback-safe read path.
    """
    source = row["source"]
    company = row["company"]
    fetched_at = row["fetched_at"]
    commit = not dry_run

    extractor = EXTRACTORS[source]
    postings = extractor.extract(row["payload"])
    postings = _deduplicate_postings(postings, source=source, company=company)
    current_ids = {p.external_id for p in postings}

    previous_payload = db.get_previous_raw_payload(conn, source=source, company=company, before=fetched_at)
    previous_count = len(extractor.extract(previous_payload)) if previous_payload is not None else None

    guard_tripped = _guard_tripped(len(postings), previous_count)

    # One query for the whole company's existing hashes, one multi-row upsert,
    # one multi-row version insert — not one round trip per posting. See
    # scripts/smoke_parse.py's history: per-posting queries exhausted local
    # ports against Railway's proxy at ~11k postings.
    existing_hashes = db.get_existing_posting_hashes(conn, source=source, company=company)
    inserted = sum(1 for p in postings if p.external_id not in existing_hashes)
    updated = len(postings) - inserted

    posting_ids = db.bulk_upsert_postings(
        conn, source=source, company=company, postings=postings, observed_at=fetched_at, commit=commit
    )
    changed_versions = [
        {
            "posting_id": posting_ids[p.external_id],
            "observed_at": fetched_at,
            "content_hash": p.content_hash,
            "title": p.title,
            "location": p.location,
            "description_raw": p.description_raw,
        }
        for p in postings
        if existing_hashes.get(p.external_id) != p.content_hash
    ]
    db.bulk_append_posting_versions(conn, versions=changed_versions, commit=commit)

    closed = 0
    if guard_tripped:
        print(
            f"WARNING: {source}/{company}: feed guard tripped "
            f"({len(postings)} seen, {previous_count} previously) — skipping close-detection"
        )
    else:
        closed = db.close_missing_postings(
            conn,
            source=source,
            company=company,
            seen_external_ids=current_ids,
            closed_at=fetched_at,
            commit=commit,
        )
        if closed:
            print(f"{source}/{company}: closed {closed} posting(s)")

    db.mark_raw_fetch_parsed(conn, row["id"], commit=commit)

    if not dry_run:
        stripped_payload, changed = strip_descriptions(row["payload"], source)
        if changed:
            db.update_raw_fetch_payload(conn, row["id"], stripped_payload, commit=commit)

    return {"inserted": inserted, "updated": updated, "closed": closed, "versions": len(changed_versions)}


def _configured_source_companies() -> set[tuple[str, str]]:
    companies = load_companies(resolve_companies_file())
    return {(source_name, token) for source_name, tokens in companies.items() for token in tokens}


def _reconcile_orphaned_companies(conn: Any, *, dry_run: bool = False) -> tuple[list[str], int]:
    """Close out postings for any (source, company) no longer in
    companies.yaml at all — e.g. removed, or moved to a different source.
    Those never appear in a feed again, so close_missing_postings never runs
    for them; without this they'd stay is_open=true forever.

    Runs unconditionally after the main row loop, including under --full, so
    a rebuild reaches the same end state as incremental runs.
    """
    configured = _configured_source_companies()
    orphaned = db.get_open_source_companies(conn) - configured

    reconciled_companies = []
    total_closed = 0
    for source, company in sorted(orphaned):
        closed = db.close_orphaned_postings(conn, source=source, company=company, commit=not dry_run)
        if closed:
            reconciled_companies.append(f"{source}/{company}")
            total_closed += closed
            print(f"{source}/{company}: reconciled — no longer in companies.yaml, closed {closed} posting(s)")

    return reconciled_companies, total_closed


def run(*, full: bool = False, dry_run: bool = False, i_know_this_destroys_history: bool = False) -> dict:
    """dry_run runs all the same SQL — including schema setup, which always
    commits, since the tables have to exist for any of this to work — but
    every write after that stays uncommitted and gets rolled back at the end.
    Nothing is persisted; the returned counts describe what would have
    changed.

    full+dry_run skips the destructive-rebuild guard below: TRUNCATE
    participates in the transaction like any other write here, so a dry-run
    --full is rolled back same as everything else and never actually
    destroys anything (see reset_parsed_state's own docstring)."""
    conn = db.get_connection()
    db.init_schema(conn)

    if full and not dry_run:
        stripped_count = db.count_stripped_raw_fetches(conn)
        if stripped_count > 0:
            if not i_know_this_destroys_history:
                conn.close()
                raise DestructiveFullRebuildRefused(
                    f"Refusing --full: {stripped_count} raw_fetches row(s) have had descriptions "
                    "stripped (scripts/prune_raw_fetches.py). --full truncates posting_versions "
                    "before rebuilding it from raw_fetches, which would destroy the only "
                    "surviving copy of description history for those rows — irrecoverably. "
                    "Re-run with --i-know-this-destroys-history to proceed anyway."
                )
            print(
                f"WARNING: proceeding with --full despite {stripped_count} raw_fetches row(s) "
                "with stripped descriptions — --i-know-this-destroys-history was passed. "
                "Description history for those rows will not survive this rebuild."
            )

    if full:
        db.reset_parsed_state(conn, commit=not dry_run)

    rows = db.get_unparsed_raw_fetches(conn)

    processed = 0
    skipped_failures = 0
    skipped_unsupported_source = 0
    totals = {"inserted": 0, "updated": 0, "closed": 0, "versions": 0}

    for row in rows:
        if row["payload"] is None:
            db.mark_raw_fetch_parsed(conn, row["id"], commit=not dry_run)
            skipped_failures += 1
            continue
        if row["source"] not in EXTRACTORS:
            # No registered extractor for this source (e.g. Workday, whose
            # extractor needs an interface change not built yet — see
            # parse/workday.py). Leave parsed_at unset so this row is
            # retried automatically once one is registered, rather than
            # requiring a --full rebuild. Must not raise: one unsupported
            # source's rows must not abort parsing for every other source.
            print(
                f"WARNING: {row['source']}/{row['company']}: no extractor registered for this source — "
                f"leaving raw_fetches row {row['id']} unparsed"
            )
            skipped_unsupported_source += 1
            continue
        stats = process_row(conn, row, dry_run=dry_run)
        for key in totals:
            totals[key] += stats[key]
        processed += 1

    reconciled_companies, postings_reconciled = _reconcile_orphaned_companies(conn, dry_run=dry_run)

    if dry_run:
        conn.rollback()
    conn.close()

    return {
        "processed": processed,
        "skipped_failures": skipped_failures,
        "skipped_unsupported_source": skipped_unsupported_source,
        "total_rows": len(rows),
        "dry_run": dry_run,
        "postings_inserted": totals["inserted"],
        "postings_updated": totals["updated"],
        "postings_closed": totals["closed"],
        "versions_appended": totals["versions"],
        "reconciled_companies": reconciled_companies,
        "postings_reconciled": postings_reconciled,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Wipe postings and posting_versions and re-parse all raw_fetches from scratch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the same SQL but roll back at the end; print what would change without persisting anything",
    )
    parser.add_argument(
        "--i-know-this-destroys-history",
        action="store_true",
        help=(
            "Override --full's refusal to run when raw_fetches rows have had descriptions "
            "stripped (scripts/prune_raw_fetches.py) — proceeding destroys posting_versions' "
            "only surviving copy of that description history, irrecoverably"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        summary = run(full=args.full, dry_run=args.dry_run, i_know_this_destroys_history=args.i_know_this_destroys_history)
    except DestructiveFullRebuildRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    prefix = "[dry run] " if args.dry_run else ""
    print(f"{prefix}Parsed {summary['processed']} row(s), skipped {summary['skipped_failures']} failed fetch(es).")
    if summary["skipped_unsupported_source"]:
        print(
            f"{prefix}skipped {summary['skipped_unsupported_source']} row(s) with no registered "
            f"extractor for their source — left unparsed for a future run"
        )
    print(
        f"{prefix}postings: {summary['postings_inserted']} inserted, "
        f"{summary['postings_updated']} updated, {summary['postings_closed']} closed"
    )
    print(f"{prefix}posting_versions: {summary['versions_appended']} appended")
    if summary["reconciled_companies"]:
        print(
            f"{prefix}reconciled {len(summary['reconciled_companies'])} company/companies no longer "
            f"in companies.yaml, closing {summary['postings_reconciled']} posting(s): "
            f"{', '.join(summary['reconciled_companies'])}"
        )


if __name__ == "__main__":
    main()
