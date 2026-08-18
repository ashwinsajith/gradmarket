#!/usr/bin/env python3
"""Dev utility: evaluate the classifier against human-labelled ground truth.

Not part of the installed gradmarket package. Joins data/eval/labels.csv to
postings by url, then runs the CURRENT classify_location/classify_seniority
functions fresh against each posting's title/location/description — not the
stored location_class/seniority_class columns. That means tuning the rules
in gradmarket/classify/ and re-running this script always reflects the
latest code, with no need to re-run classify_run first.

Splits deterministically by hashing the url (not randomly, and not by row
order): 60% dev, 40% test. A url's split never changes as more labels get
added, and results are reproducible across machines/runs with no seed to
manage. Run directly:

    python scripts/evaluate_classifier.py [--split {dev,test}]

split defaults to dev. Tune against dev only — see the warning this prints
when run on test.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

from gradmarket import db
from gradmarket.classify.location import classify_location
from gradmarket.classify.seniority import classify_seniority

LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "labels.csv"
DEV_FRACTION = 0.6
HASH_SPACE = 2**32

LOCATION_CLASSES = ["uk", "non_uk", "unknown"]
SENIORITY_CLASSES = ["early", "experienced", "unknown"]


def split_for_url(url: str) -> str:
    """Deterministic dev/test assignment: hash the url, dev if the hash
    falls in the first DEV_FRACTION of the space. Stable regardless of
    labelling order or how many labels exist at any given time."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / HASH_SPACE
    return "dev" if fraction < DEV_FRACTION else "test"


def load_labels(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"labels file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_postings_by_url(conn, urls: list[str]) -> dict[str, dict]:
    """url -> {title, location, description_raw}. One query, not one per url."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.url, p.title, p.location, pv.description_raw
            FROM postings p
            LEFT JOIN LATERAL (
                SELECT description_raw
                FROM posting_versions
                WHERE posting_id = p.id
                ORDER BY observed_at DESC
                LIMIT 1
            ) pv ON true
            WHERE p.url = ANY(%s)
            """,
            (urls,),
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    return {row["url"]: row for row in rows}


def confusion_matrix(pairs: list[tuple[str, str]], classes: list[str]) -> dict[tuple[str, str], int]:
    """pairs: (true, predicted). Returns {(true, predicted): count} for every
    class combination, including zero counts."""
    matrix = {(t, p): 0 for t in classes for p in classes}
    for true, pred in pairs:
        matrix[(true, pred)] = matrix.get((true, pred), 0) + 1
    return matrix


def print_confusion_matrix(pairs: list[tuple[str, str]], classes: list[str], name: str) -> None:
    matrix = confusion_matrix(pairs, classes)
    width = max(len(c) for c in classes + ["true\\pred"]) + 2

    print(f"\n{name} confusion matrix (rows=true, columns=predicted):")
    print("true\\pred".ljust(width) + "".join(c.ljust(width) for c in classes))
    for t in classes:
        row = t.ljust(width) + "".join(str(matrix[(t, p)]).ljust(width) for p in classes)
        print(row)


def precision_recall_f1(pairs: list[tuple[str, str]], classes: list[str]) -> dict[str, dict[str, float]]:
    result = {}
    for c in classes:
        tp = sum(1 for t, p in pairs if t == c and p == c)
        fp = sum(1 for t, p in pairs if t != c and p == c)
        fn = sum(1 for t, p in pairs if t == c and p != c)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        result[c] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return result


def print_precision_recall_f1(pairs: list[tuple[str, str]], classes: list[str], name: str) -> None:
    scores = precision_recall_f1(pairs, classes)
    print(f"\n{name} precision / recall / F1:")
    for c in classes:
        s = scores[c]
        print(
            f"  {c:<12} precision={s['precision']:.2f}  recall={s['recall']:.2f}  "
            f"f1={s['f1']:.2f}  (support={s['support']})"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.split == "test":
        print("=" * 72)
        print("WARNING: running on the TEST split.")
        print("This number is only honest if the classifier rules were NOT tuned")
        print("against anything you've seen from test before. If you've looked at")
        print("test failures and adjusted gradmarket/classify/ in response, this")
        print("number no longer measures what you think it measures.")
        print("=" * 72)

    labels = load_labels(LABELS_PATH)
    split_labels = [row for row in labels if split_for_url(row["url"]) == args.split]

    conn = db.get_connection()
    postings_by_url = fetch_postings_by_url(conn, [row["url"] for row in split_labels])
    conn.close()

    location_pairs = []
    seniority_pairs = []
    misclassified = []
    unresolved = []

    for row in split_labels:
        posting = postings_by_url.get(row["url"])
        if posting is None:
            unresolved.append(row)
            continue

        pred_location = classify_location(posting["location"])
        pred_seniority = classify_seniority(posting["title"], posting["description_raw"])

        location_pairs.append((row["label_location"], pred_location))
        seniority_pairs.append((row["label_seniority"], pred_seniority))

        if pred_location != row["label_location"] or pred_seniority != row["label_seniority"]:
            misclassified.append(
                {
                    "title": posting["title"],
                    "location": posting["location"],
                    "url": row["url"],
                    "true_location": row["label_location"],
                    "pred_location": pred_location,
                    "true_seniority": row["label_seniority"],
                    "pred_seniority": pred_seniority,
                }
            )

    print(f"\n[{args.split}] {len(split_labels)} labelled row(s), {len(unresolved)} unresolved (url not found)")

    print_confusion_matrix(location_pairs, LOCATION_CLASSES, "location")
    print_precision_recall_f1(location_pairs, LOCATION_CLASSES, "location")

    print_confusion_matrix(seniority_pairs, SENIORITY_CLASSES, "seniority")
    print_precision_recall_f1(seniority_pairs, SENIORITY_CLASSES, "seniority")

    print(f"\nmisclassified ({len(misclassified)} of {len(location_pairs)}):")
    for m in misclassified:
        print(
            f"  {m['title']!r} | {m['location']!r}\n"
            f"    location:  true={m['true_location']:<10} pred={m['pred_location']}\n"
            f"    seniority: true={m['true_seniority']:<10} pred={m['pred_seniority']}\n"
            f"    {m['url']}"
        )

    if unresolved:
        print(f"\nunresolved ({len(unresolved)}) — url has no match in postings:")
        for row in unresolved:
            print(f"  {row['url']}")


if __name__ == "__main__":
    main()
