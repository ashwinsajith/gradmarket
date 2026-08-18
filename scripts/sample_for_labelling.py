#!/usr/bin/env python3
"""Dev utility: sample open postings for manual labelling.

Not part of the installed gradmarket package. Pulls a stratified random
sample of open postings, jointly stratified on two axes — UK/non-UK location
and early-careers/not title — targeting an even split across the 4 cells,
since a pure random sample skews heavily toward US, senior-titled roles and
wastes most of the labelling effort on postings from buckets you don't
actually need more examples of. Deterministic seed, so re-running with the
same --seed reproduces the same sample.

--exclude takes a path to an already-labelled CSV (e.g. a prior sample or
data/eval/labels.csv) and drops any url labelled there from the candidate
pool, so you never label the same posting twice across samples. Matches on
url rather than posting_id, since a --full parse rebuild resets the id
sequence and makes old ids unstable — see scripts/rekey_labels.py.

Writes into data/eval/, which is gitignored except for labels.csv: the
description snippet is company-owned content (see CLAUDE.md). Run directly:

    python scripts/sample_for_labelling.py [options]

Options (all optional):
  --exclude PATH          CSV whose labelled urls should never be re-sampled
  --output PATH           default: data/eval/sample.csv
  --sample-size N         default: 100
  --seed N                default: 42
  --snippet-length N      default: 1500
"""

from __future__ import annotations

import argparse
import csv
import html
import random
import re
from pathlib import Path

from gradmarket import db

DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "sample.csv"
DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SEED = 42
# 300 chars only ever captured company boilerplate ("About us...") and never
# reached the actual requirements section — 1500 gets meaningfully further
# into the posting without pulling in the entire description.
DEFAULT_SNIPPET_LENGTH = 1500

FIELDNAMES = [
    "posting_id",
    "source",
    "company",
    "title",
    "location",
    "url",
    "description_snippet",
    "label_location",
    "label_seniority",
]

# Rough on purpose: these are free-text location strings from three different
# ATSes ("London, UK", "Remote (UK)", "London, England"...), not geocoded.
# Good enough to stratify a sample, not to publish as ground truth.
UK_LOCATION_PATTERN = re.compile(
    r"\b("
    r"uk|u\.k\.|united kingdom|england|scotland|wales|northern ireland|"
    r"london|manchester|edinburgh|glasgow|bristol|birmingham|leeds|"
    r"cambridge|oxford|cardiff|belfast|liverpool|sheffield|newcastle|reading"
    r")\b",
    re.IGNORECASE,
)

# Also rough: title text, not a seniority classifier. Good enough to
# stratify, not to publish as ground truth.
EARLY_CAREER_TITLE_PATTERN = re.compile(
    r"\b("
    r"graduate|intern|internship|junior|campus|placement|trainee|apprentice|"
    r"new grad|summer analyst|early career"
    r")\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    # Some ATS content comes back HTML-entity-escaped (literal "&lt;p&gt;"
    # instead of "<p>"), occasionally doubly so — unescape before stripping
    # tags, twice, since unescaping already-plain text is a harmless no-op.
    text = html.unescape(html.unescape(text))
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_uk(location: str | None) -> bool:
    return bool(location) and UK_LOCATION_PATTERN.search(location) is not None


def is_early_career(title: str | None) -> bool:
    return bool(title) and EARLY_CAREER_TITLE_PATTERN.search(title) is not None


def fetch_open_postings(conn) -> list[dict]:
    """Every open posting, joined to its latest version for the description."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.source, p.company, p.title, p.location, p.url, pv.description_raw
            FROM postings p
            LEFT JOIN LATERAL (
                SELECT description_raw
                FROM posting_versions
                WHERE posting_id = p.id
                ORDER BY observed_at DESC
                LIMIT 1
            ) pv ON true
            WHERE p.is_open = true
            ORDER BY p.id
            """
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def load_excluded_urls(path: Path) -> set[str]:
    """URLs already labelled (both columns filled) in an existing sample CSV
    — rows still awaiting labels there are not excluded, since they're not
    yet ground truth and re-sampling them isn't the problem this guards
    against.

    Matches on url, not posting_id: a --full rebuild TRUNCATEs postings and
    resets its id sequence, so ids from an older sample no longer correspond
    to the same rows. url is stable across that."""
    if not path.is_file():
        raise FileNotFoundError(f"exclude file not found: {path}")
    excluded = set()
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("label_location") and row.get("label_seniority"):
                excluded.add(row["url"])
    return excluded


def stratified_sample(pools: dict[object, list[dict]], total: int, rng: random.Random) -> list[dict]:
    """Splits `total` evenly across however many cells `pools` has. If a cell
    is short, the shortfall is backfilled round-robin from cells with spare
    capacity, rather than shrinking the total below what's available."""
    for pool in pools.values():
        rng.shuffle(pool)

    target_each = total // len(pools)
    taken = {key: min(target_each, len(pool)) for key, pool in pools.items()}

    shortfall = total - sum(taken.values())
    while shortfall > 0:
        progressed = False
        for key, pool in pools.items():
            if shortfall <= 0:
                break
            if len(pool) - taken[key] > 0:
                taken[key] += 1
                shortfall -= 1
                progressed = True
        if not progressed:
            break  # every cell exhausted; sample will come up short of total

    sample = [p for key, pool in pools.items() for p in pool[: taken[key]]]
    rng.shuffle(sample)
    return sample


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--exclude",
        type=Path,
        default=None,
        help="Path to an existing labelled CSV; urls already labelled there are never re-sampled",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Total rows to sample (default: {DEFAULT_SAMPLE_SIZE})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for reproducibility (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--snippet-length",
        type=int,
        default=DEFAULT_SNIPPET_LENGTH,
        help=f"description_snippet length in characters (default: {DEFAULT_SNIPPET_LENGTH})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    excluded_urls = load_excluded_urls(args.exclude) if args.exclude else set()

    conn = db.get_connection()
    postings = fetch_open_postings(conn)
    conn.close()

    if excluded_urls:
        before = len(postings)
        postings = [p for p in postings if p["url"] not in excluded_urls]
        print(f"excluded {before - len(postings)} already-labelled posting(s) from {args.exclude}")

    cells: dict[tuple[bool, bool], list[dict]] = {
        (True, True): [],
        (True, False): [],
        (False, True): [],
        (False, False): [],
    }
    for p in postings:
        cells[(is_uk(p["location"]), is_early_career(p["title"]))].append(p)

    print(f"{len(postings)} open postings available after exclusions")
    for (uk, early), pool in cells.items():
        print(f"  UK={uk} early-career={early}: {len(pool)} available")

    rng = random.Random(args.seed)
    sample = stratified_sample(cells, args.sample_size, rng)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDNAMES)
        for p in sample:
            snippet = strip_html(p["description_raw"] or "")[: args.snippet_length]
            writer.writerow(
                [p["id"], p["source"], p["company"], p["title"], p["location"], p["url"], snippet, "", ""]
            )

    uk_in_sample = sum(1 for p in sample if is_uk(p["location"]))
    early_in_sample = sum(1 for p in sample if is_early_career(p["title"]))
    print(
        f"wrote {len(sample)} postings to {args.output} "
        f"({uk_in_sample} UK-matching, {len(sample) - uk_in_sample} other; "
        f"{early_in_sample} early-career, {len(sample) - early_in_sample} other)"
    )
    if len(sample) < args.sample_size:
        print(f"warning: only {len(sample)} open postings available, short of the {args.sample_size} target")


if __name__ == "__main__":
    main()
