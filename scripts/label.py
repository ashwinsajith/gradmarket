#!/usr/bin/env python3
"""Interactive terminal labelling tool for a sample_for_labelling.py CSV.

Not part of the installed gradmarket package. Single-keypress prompts (no
Enter needed) for two labels per row: location (u=uk, n=non_uk, x=unknown)
and seniority (e=early, x=experienced, u=unknown). The two prompts reuse
letters for different meanings (x is "unknown" for location but
"experienced" for seniority) — that's fine, each prompt's own keys are
unambiguous on their own.

Writes the CSV back after every completed row, so quitting (q) loses
nothing. b goes back and redoes the previous row. Resuming skips any row
that already has both labels.

Every save also syncs data/eval/labels.csv — url, label_location,
label_seniority only, no snippets or other company-owned content — which is
the committed file; the working CSV given on the command line stays
gitignored. The sync also runs once up front, so labels already present in
the working file get exported even if there's nothing left to label this
session. labels.csv accumulates across working files regardless of which one
you point this at, keyed by url, so it isn't wiped out by regenerating or
switching sample files. Run directly:

    python scripts/label.py [file]

file defaults to data/eval/sample.csv.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import termios
import textwrap
import tty
from pathlib import Path

DEFAULT_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "sample.csv"
LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "labels.csv"
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
LABELS_FIELDNAMES = ["url", "label_location", "label_seniority"]

LOCATION_KEYS = {"u": "uk", "n": "non_uk", "x": "unknown"}
SENIORITY_KEYS = {"e": "early", "x": "experienced", "u": "unknown"}

QUIT = "__quit__"
BACK = "__back__"


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def is_labelled(row: dict) -> bool:
    return bool(row.get("label_location")) and bool(row.get("label_seniority"))


def load_labels(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["url"]: row for row in csv.DictReader(f)}


def sync_labels_file(rows: list[dict], labels_path: Path) -> None:
    """Merge every labelled row's (url, label_location, label_seniority)
    into labels.csv, keyed by url — additive across working files, never
    drops entries this run's `rows` doesn't happen to include."""
    labels = load_labels(labels_path)
    for row in rows:
        if is_labelled(row):
            labels[row["url"]] = {
                "url": row["url"],
                "label_location": row["label_location"],
                "label_seniority": row["label_seniority"],
            }

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LABELS_FIELDNAMES)
        writer.writeheader()
        for url in sorted(labels):
            writer.writerow(labels[url])


def read_key() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def show_row(row: dict, index: int, total: int) -> None:
    clear_screen()
    width = shutil.get_terminal_size(fallback=(80, 24)).columns
    print(f"[{index + 1} of {total}]\n")
    print(f"Company:  {row['company']}")
    print(f"Title:    {row['title']}")
    print(f"Location: {row['location']}")
    print(f"URL:      {row['url']}\n")
    print(textwrap.fill(row["description_snippet"], width=width))
    print()


def prompt(label: str, keys: dict) -> str:
    options = "  ".join(f"[{k}]={v}" for k, v in keys.items())
    while True:
        print(f"{label}? {options}   (q=quit  b=back)")
        key = read_key()
        if key == "q":
            print("> quit\n")
            return QUIT
        if key == "b":
            print("> back\n")
            return BACK
        if key in keys:
            print(f"> {keys[key]}\n")
            return keys[key]
        print(f"> invalid key {key!r}, try again\n")


def label_row(row: dict, index: int, total: int) -> str:
    """Labels one row in place. Returns 'done', QUIT, or BACK."""
    show_row(row, index, total)

    location = prompt("Location", LOCATION_KEYS)
    if location in (QUIT, BACK):
        return location

    seniority = prompt("Seniority", SENIORITY_KEYS)
    if seniority in (QUIT, BACK):
        return seniority

    row["label_location"] = location
    row["label_seniority"] = seniority
    return "done"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "file",
        nargs="?",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="CSV to label (default: data/eval/sample.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = args.file

    if not csv_path.is_file():
        print(f"error: {csv_path} not found — run scripts/sample_for_labelling.py first", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(csv_path)
    total = len(rows)
    to_visit = [i for i, row in enumerate(rows) if not is_labelled(row)]

    # Exports whatever's already labelled even if there's nothing left to do
    # this session — e.g. the first run after labels.csv was introduced.
    sync_labels_file(rows, LABELS_PATH)

    if not to_visit:
        print("All rows already labelled.")
        return

    position = 0
    try:
        while position < len(to_visit):
            idx = to_visit[position]
            result = label_row(rows[idx], idx, total)

            if result == "done":
                save_rows(csv_path, rows)
                sync_labels_file(rows, LABELS_PATH)
                position += 1
            elif result == BACK:
                if position == 0:
                    print("Already at the first row of this session — press any key.")
                    read_key()
                else:
                    position -= 1
            elif result == QUIT:
                clear_screen()
                done = sum(1 for row in rows if is_labelled(row))
                print(f"Stopped. {done} of {total} labelled. Resume any time — progress is saved.")
                return
    except KeyboardInterrupt:
        clear_screen()
        done = sum(1 for row in rows if is_labelled(row))
        print(f"Interrupted. {done} of {total} labelled. Resume any time — progress is saved.")
        return

    clear_screen()
    print(f"Done — all {total} rows labelled.")


if __name__ == "__main__":
    main()
