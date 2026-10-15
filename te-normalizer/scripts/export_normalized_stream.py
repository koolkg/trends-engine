#!/usr/bin/env python3
"""Writes what te-normalizer would publish for te-connector's fixture, as a
labeled stream for te-detector's and te-cluster's offline tests and evals
(their tests/fixtures/normalized_stream.jsonl). Same cleanup, classifier and
dedup as the service, minus Postgres and RabbitMQ. Rerun after regenerating
the fixture or changing the classifier:

    python scripts/export_normalized_stream.py \\
        ../te-connector/fixtures/raw_content.jsonl ../te-connector/fixtures/raw_content.labels.jsonl \\
        ../te-detector/tests/fixtures/normalized_stream.jsonl ../te-cluster/tests/fixtures/normalized_stream.jsonl
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from normalizer.classifier import classify, is_relevant  # noqa: E402
from normalizer.cleanup import clean_text  # noqa: E402
from normalizer.dedup import dedup_hash  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("labels")
    ap.add_argument("out", nargs="+")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    labels = {}
    with open(a.labels, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            labels[row["contentId"]] = row["trend"] or row["kind"]
    seen, rows = set(), []
    with open(a.fixture, encoding="utf-8") as f:
        for line in f:
            post = json.loads(line)
            cleaned = clean_text(post["rawText"])
            if not is_relevant(classify(cleaned), a.threshold):
                continue
            h = dedup_hash(cleaned)
            if h in seen:
                continue
            seen.add(h)
            rows.append({"postedAt": post["postedAt"], "cleanedText": cleaned, "label": labels[post["contentId"]]})
    for path in a.out:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} posts -> {', '.join(a.out)}")


if __name__ == "__main__":
    main()
