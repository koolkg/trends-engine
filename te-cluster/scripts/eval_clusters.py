#!/usr/bin/env python3
"""Cluster quality on the labeled fixture stream, for picking the join threshold.

    python scripts/eval_clusters.py                       # real MiniLM, thresholds 0.5-0.8
    python scripts/eval_clusters.py --thresholds 0.6 0.72
    python scripts/eval_clusters.py --fake                # stand-in embedder (no model needed)

Reports, per threshold:
  clusters / singletons: how fragmented the result is
  purity:   among clusters with >=5 members, the share of posts matching
            their cluster's majority label (1.0 = no mixing)
  coverage: per trend, the share of its posts in its single biggest cluster
            (1.0 = the whole trend landed in one cluster)
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cluster.centroids import CentroidIndex  # noqa: E402

STREAM = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "normalized_stream.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", default=STREAM)
    ap.add_argument("--thresholds", type=float, nargs="+", default=[0.5, 0.6, 0.65, 0.7, 0.72, 0.75, 0.8])
    ap.add_argument("--fake", action="store_true", help="use the bag-of-words stand-in instead of MiniLM")
    a = ap.parse_args()
    rows = [json.loads(line) for line in open(a.stream, encoding="utf-8")]
    if a.fake:
        from tests.fake_embedder import FakeEmbedder
        emb = FakeEmbedder()
    else:
        from cluster.embedder import MiniLMEmbedder
        emb = MiniLMEmbedder()
    vectors = emb.encode([r["cleanedText"] for r in rows])
    print(f"{len(rows)} posts embedded ({'stand-in' if a.fake else 'all-MiniLM-L6-v2'})\n")
    trends = sorted({r["label"] for r in rows if r["label"] != "evergreen"})
    print(f"{'thresh':>6} {'clusters':>8} {'single':>7} {'purity':>7}  " + "  ".join(t[:10] for t in trends))
    for th in a.thresholds:
        idx = CentroidIndex(emb.dim, th)
        members = defaultdict(list)
        for r, v in zip(rows, vectors):
            when = datetime.fromisoformat(r["postedAt"].replace("Z", "+00:00"))
            members[idx.assign(v, when).cluster_id].append(r["label"])
        big = [labels for labels in members.values() if len(labels) >= 5]
        purity = sum(Counter(l).most_common(1)[0][1] for l in big) / max(1, sum(len(l) for l in big))
        singles = sum(len(l) == 1 for l in members.values())
        cov = []
        for t in trends:
            total = sum(r["label"] == t for r in rows)
            best = max(l.count(t) for l in members.values())
            cov.append(f"{best / total:10.2f}")
        print(f"{th:6.2f} {len(members):8d} {singles:7d} {purity:7.2f}  " + "  ".join(cov))


if __name__ == "__main__":
    main()
