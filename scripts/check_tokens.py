#!/usr/bin/env python3
"""Dev utility: probe candidate tokens for validity against a source's board API.

Not part of the installed gradmarket package. Calls the same fetch() used in
production (gradmarket.sources), so results always match what ingest.py would
see. Run directly:

    python scripts/check_tokens.py [candidates_file] [--source {greenhouse,lever,ashby,workable,workday}]

For --source workable specifically: a nonsense token 404s, so a 200 response
— even with zero jobs — confirms a real, resolved account, same as the other
three sources. What's still separated out is a small "likely shell" bucket:
a zero-job account is only excluded from the paste block when BOTH its name
is token-identical (case-insensitive) AND its description is empty — see
workable_likely_shell() and CLAUDE.md's data model section. Workable account
slugs still aren't namespaced to real company identity (the slug-collision
finding, e.g. "notion" resolving to an unrelated agency, is unrelated to and
unaffected by the job-count question) — see CLAUDE.md.

For --source workday specifically: a candidate isn't a bare token, it's four
comma-separated values per line — company,tenant,dc,site (see
DEFAULT_WORKDAY_CANDIDATES_FILE and read_workday_candidates()) — because a
Workday board is identified by three coordinates, not one (see CLAUDE.md's
data model notes and gradmarket.sources.workday.WorkdayToken). A working
candidate's report includes a sample of titles and locations (SAMPLE_SIZE),
not just a job count — job count alone doesn't tell you whether a board is
actually worth adding, and there's no per-posting detail fetch at discovery
time to look at instead (that's a separate, paced pass — detail_run.py — not
something this script should trigger for a large candidate list). The
paste-ready output for workday is a full `workday:` YAML block, structured
objects and all, not just a bare list — unlike the other sources, there's
usually no existing `workday:` key in companies.yaml yet to paste a bare list
under.

Retries for 429/5xx are handled inside each source's own fetch() (the same
fetch() production uses) — this script doesn't reimplement that. What it adds
on top is discovery-time pacing between DIFFERENT tokens (--sleep) and
reporting: a token that still comes back 429 after fetch() exhausts its own
retries is a source we rate-limited ourselves into never actually testing,
not a source that doesn't exist. It's reported separately ("rate limited,
not tested") from a real 404, and every run's per-token status is recorded
to a results file so a later run can retest just that subset:

    python scripts/check_tokens.py --source workable --sleep 3
    python scripts/check_tokens.py --source workable --sleep 5 --only-status 429
    python scripts/check_tokens.py --source workday --sleep 2

--only-status reads token statuses from the results file (default:
scripts/check_tokens_results_<source>.csv, one per source so runs against
different sources don't clobber each other) rather than from candidates_file,
so on a --only-status run candidates_file is ignored. The results file is
merged, not overwritten, on every run — retesting one status doesn't lose the
recorded statuses of tokens you didn't retest. For workday, the results file
also carries tenant/dc/site per row, so --only-status can reconstruct a full
WorkdayToken to retest without re-reading candidates_file.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from gradmarket.sources import SOURCES
from gradmarket.sources.workday import WorkdayToken

DEFAULT_CANDIDATES_FILE = Path(__file__).resolve().parent / "candidates.txt"
DEFAULT_WORKDAY_CANDIDATES_FILE = Path(__file__).resolve().parent / "candidates_workday.txt"
DEFAULT_SLEEP = 1.0
SAMPLE_SIZE = 3
RESULTS_FIELDS = ["token", "status_code", "label", "job_count", "name", "description_present", "tenant", "dc", "site"]

WORKABLE_SHELL_NOTE = """\
# A nonsense Workable token 404s — it doesn't 200 with zero jobs — so a 200
# here is a real, resolved account, same as the other three sources' empty
# boards (see CLAUDE.md's empty-but-200 gotcha). These specific accounts are
# flagged as likely shells rather than dormant real companies because BOTH:
#   - the account name is token-identical (case-insensitive), and
#   - the account description is empty
# Workable slugs still aren't namespaced to real company identity (e.g.
# "notion" resolves to an unrelated London agency) — that risk is separate
# from and unaffected by this job-count check. Verify by hand before adding
# any of these to companies.yaml."""

RATE_LIMITED_NOTE = """\
# These returned 429 on every attempt, including fetch()'s own retries with
# backoff — we rate-limited ourselves into never actually testing them. They
# are NOT confirmed missing the way a 404 is. Retest with a longer --sleep:
#   python scripts/check_tokens.py --source SOURCE --sleep 5 --only-status 429"""


def default_results_file(source: str) -> Path:
    return Path(__file__).resolve().parent / f"check_tokens_results_{source}.csv"


def read_tokens(path: Path) -> list[str]:
    tokens = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def read_workday_candidates(path: Path) -> list[WorkdayToken]:
    """Each line is company,tenant,dc,site — a Workday board needs three
    coordinates, not one bare token (see module docstring). Malformed lines
    are skipped with a warning rather than raising, same spirit as a bad
    token elsewhere in this script just failing its own probe."""
    candidates = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4 or not all(parts):
            print(f"warning: skipping malformed line (expected company,tenant,dc,site): {line!r}", file=sys.stderr)
            continue
        company, tenant, dc, site = parts
        candidates.append(WorkdayToken(company=company, tenant=tenant, dc=dc, site=site))
    return candidates


def workday_sample(payload: list[dict]) -> list[str]:
    """Up to SAMPLE_SIZE "title — location" lines from a working board's job
    list, so a job count alone doesn't have to carry the "is this worth
    adding" judgment call."""
    return [f"{job.get('title') or '?'} — {job.get('locationsText') or '?'}" for job in payload[:SAMPLE_SIZE]]


def load_results(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["token"]: row for row in csv.DictReader(f)}


def save_results(path: Path, results: dict[str, dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_FIELDS)
        writer.writeheader()
        for token in sorted(results):
            writer.writerow(results[token])


def workable_likely_shell(token: str, name: str | None, description: str | None) -> bool:
    """True only when there's no positive evidence this is a real, distinct
    account: name is missing-or-token-identical (case-insensitive) AND
    description is missing-or-empty. Either signal alone is enough to call
    it likely real — see CLAUDE.md's data model section for why zero jobs
    alone isn't evidence of a wrong company."""
    name_differs = name is not None and name.strip().lower() != token.strip().lower()
    has_description = bool(description and description.strip())
    return not (name_differs or has_description)


def status_label(result) -> str:
    if result.status_code == 200 and result.payload is None:
        return f"200 (bad payload: {result.error})"
    if result.status_code == 429:
        return "429 (rate limited, not tested — retries exhausted, existence unknown)"
    if result.status_code is not None:
        return str(result.status_code)
    return f"error: {result.error}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "candidates_file",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the candidates list — one token per line, or for --source workday, "
        "one company,tenant,dc,site per line (default: scripts/candidates.txt, or "
        "scripts/candidates_workday.txt for --source workday). Ignored when --only-status is given.",
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default="greenhouse",
        help="Source to probe tokens against (default: greenhouse)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP,
        help=f"Seconds to sleep between requests (default: {DEFAULT_SLEEP}). "
        "Raise this for sources that rate-limit aggressively (e.g. Workable).",
    )
    parser.add_argument(
        "--only-status",
        type=int,
        default=None,
        help="Only retest tokens that had this HTTP status in a previous run, "
        "per the results file (see --results-file). Ignores candidates_file.",
    )
    parser.add_argument(
        "--results-file",
        type=Path,
        default=None,
        help="Where per-token results are recorded across runs "
        "(default: scripts/check_tokens_results_<source>.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    is_workday = args.source == "workday"
    results_file = args.results_file or default_results_file(args.source)
    existing_results = load_results(results_file)

    if args.only_status is not None:
        matching_rows = [row for row in existing_results.values() if row["status_code"] == str(args.only_status)]
        if not matching_rows:
            print(
                f"no tokens in {results_file} had status {args.only_status} "
                "(run once without --only-status first)",
                file=sys.stderr,
            )
            sys.exit(1)
        candidates: list[Any] = (
            [WorkdayToken(company=row["token"], tenant=row["tenant"], dc=row["dc"], site=row["site"]) for row in matching_rows]
            if is_workday
            else [row["token"] for row in matching_rows]
        )
        print(f"retesting {len(candidates)} token(s) that previously returned {args.only_status}")
    else:
        candidates_file = args.candidates_file or (
            DEFAULT_WORKDAY_CANDIDATES_FILE if is_workday else DEFAULT_CANDIDATES_FILE
        )
        if not candidates_file.is_file():
            print(f"error: candidates file not found: {candidates_file}", file=sys.stderr)
            sys.exit(1)
        candidates = read_workday_candidates(candidates_file) if is_workday else read_tokens(candidates_file)

    fetch = SOURCES[args.source].fetch
    working: list[tuple[Any, int]] = []
    shell: list[tuple[Any, int]] = []  # only split out separately for workable
    rate_limited: list[tuple[Any, str]] = []
    failed: list[tuple[Any, str]] = []

    for i, candidate in enumerate(candidates):
        # The plain identity string for display/results-keying — a bare
        # token already is one; WorkdayToken.__str__ returns its company
        # (see gradmarket.sources.workday), same convention ingest.py and
        # parse_run.py use.
        key = str(candidate)
        result = fetch(candidate)
        label = status_label(result)
        name = None
        description_present = None

        if result.payload is not None:
            if args.source == "workable":
                name = result.payload.get("name")
                description_present = bool((result.payload.get("description") or "").strip())
                audit = f"  name={name!r}  desc={'present' if description_present else 'empty'}"
            else:
                audit = ""

            if args.source == "workable" and result.job_count == 0 and workable_likely_shell(
                key, name, result.payload.get("description")
            ):
                shell.append((candidate, result.job_count))
                print(f"{key}  {result.status_code}  {result.job_count} jobs{audit}  [LIKELY SHELL — see note below]")
            else:
                working.append((candidate, result.job_count))
                print(f"{key}  {result.status_code}  {result.job_count} jobs{audit}")
                if is_workday:
                    for line in workday_sample(result.payload):
                        print(f"    - {line}")
        elif result.status_code == 429:
            rate_limited.append((candidate, label))
            print(f"{key}  {label}")
        else:
            failed.append((candidate, label))
            print(f"{key}  {label}")

        existing_results[key] = {
            "token": key,
            "status_code": str(result.status_code) if result.status_code is not None else "",
            "label": label,
            "job_count": str(result.job_count) if result.payload is not None else "",
            "name": name if name is not None else "",
            "description_present": (
                "" if description_present is None else str(description_present)
            ),
            "tenant": candidate.tenant if is_workday else "",
            "dc": candidate.dc if is_workday else "",
            "site": candidate.site if is_workday else "",
        }

        if i < len(candidates) - 1:
            time.sleep(args.sleep)

    save_results(results_file, existing_results)

    if is_workday:
        print("\n# Working boards (workday) — paste-ready")
        block = {
            "workday": [
                {"company": c.company, "tenant": c.tenant, "dc": c.dc, "site": c.site} for c, _ in working
            ]
        }
        print(yaml.dump(block, default_flow_style=False, sort_keys=False))
    else:
        print(f"\n# Working tokens ({args.source}) — paste into companies.yaml")
        print(yaml.dump([token for token, _ in working], default_flow_style=False, sort_keys=False))

    if args.source == "workable" and shell:
        print(f"# Likely-shell tokens ({len(shell)}) — NOT included above")
        print(WORKABLE_SHELL_NOTE)
        for token, count in shell:
            print(f"  {token}: {count} jobs")
        print()

    if rate_limited:
        print(f"# Rate limited, not tested ({len(rate_limited)}) — NOT the same as a 404")
        print(RATE_LIMITED_NOTE)
        for token, _ in rate_limited:
            print(f"  {token}")
        print()

    print("# Failed tokens")
    for token, label in failed:
        print(f"{token}: {label}")

    print(f"\n# Results recorded to {results_file}")


if __name__ == "__main__":
    main()
