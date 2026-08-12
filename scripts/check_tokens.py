#!/usr/bin/env python3
"""Dev utility: probe candidate tokens for validity against a source's board API.

Not part of the installed gradmarket package. Calls the same fetch() used in
production (gradmarket.sources), so results always match what ingest.py would
see. Run directly:

    python scripts/check_tokens.py [candidates_file] [--source {greenhouse,lever,ashby}]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from gradmarket.sources import SOURCES

DEFAULT_CANDIDATES_FILE = Path(__file__).resolve().parent / "candidates.txt"
INTER_REQUEST_SLEEP = 1


def read_tokens(path: Path) -> list[str]:
    tokens = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def status_label(result) -> str:
    if result.status_code == 200 and result.payload is None:
        return f"200 (bad payload: {result.error})"
    if result.status_code is not None:
        return str(result.status_code)
    return f"error: {result.error}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "candidates_file",
        nargs="?",
        type=Path,
        default=DEFAULT_CANDIDATES_FILE,
        help="Path to a newline-delimited token list (default: scripts/candidates.txt)",
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default="greenhouse",
        help="Source to probe tokens against (default: greenhouse)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.candidates_file.is_file():
        print(f"error: candidates file not found: {args.candidates_file}", file=sys.stderr)
        sys.exit(1)

    fetch = SOURCES[args.source].fetch
    tokens = read_tokens(args.candidates_file)
    working: list[tuple[str, int]] = []
    failed: list[tuple[str, str]] = []

    for i, token in enumerate(tokens):
        result = fetch(token)
        if result.payload is not None:
            working.append((token, result.job_count))
            print(f"{token}  {result.status_code}  {result.job_count} jobs")
        else:
            label = status_label(result)
            failed.append((token, label))
            print(f"{token}  {label}")

        if i < len(tokens) - 1:
            time.sleep(INTER_REQUEST_SLEEP)

    print(f"\n# Working tokens ({args.source}) — paste into companies.yaml")
    print(yaml.dump([token for token, _ in working], default_flow_style=False, sort_keys=False))

    print("# Failed tokens")
    for token, label in failed:
        print(f"{token}: {label}")


if __name__ == "__main__":
    main()
