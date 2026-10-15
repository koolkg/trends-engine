#!/usr/bin/env python3
"""Scores the relevance + category decision against te-connector's fixture ground truth.

    python scripts/eval_classifier.py [fixture.jsonl] [labels.jsonl] [--threshold 0.5] [--misses] [--sweep]

Relevance: noise should be dropped, trend + evergreen kept. Category: each
trend's expected category below (evergreen has no single expected category).

Reports three variants side by side, so a change to either classifier is
measured before it ships:
  keywords   the keyword table alone (the pre-NORM-9 behaviour, and the backup)
  model      the model alone: keep only when it is sure
  combined   what the service runs: model first, keywords as backup/validator
The model variants need sentence-transformers (run via `make eval`, which
uses the service image with the model baked in); without it only keywords run.
--sweep prints the combined result for a range of "sure" thresholds.
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from normalizer.cleanup import clean_text  # noqa: E402
from normalizer.relevance import KeywordRelevance, ModelRelevance  # noqa: E402

EXPECTED_CATEGORY = {
    "dupe_culture": "ecommerce",
    "pov_product_demos": "social",
    "lowercase_brand_voice": "social",
    "numbers_subject_lines": "email",
    "founder_voice_cold_opens": "email",
    "quiet_luxury_fatigue": "content",
    "girl_dinner_microcontent": "content",
}
DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "te-connector", "fixtures")


class Cached:
    """Model results computed once for the whole fixture, replayed per variant."""

    def __init__(self, results):
        self.results, self.i = results, 0

    def classify(self, _text):
        r = self.results[self.i]
        self.i += 1
        return r


def score(name, texts, labs, decider, misses=False, quiet=False):
    tp = fp = fn = tn = 0
    cat_ok = cat_n = 0
    per_trend = {}
    methods = Counter()
    for text, l in zip(texts, labs):
        d = decider.decide(text)
        methods[d.method] += 1
        truth = l["kind"] != "noise"
        tp += d.relevant and truth; fp += d.relevant and not truth
        fn += truth and not d.relevant; tn += not d.relevant and not truth
        if misses and d.relevant != truth:
            print(f"  {'FP' if d.relevant else 'FN'} {d.relevance_score:.2f} {d.method:17} {l['trend'] or l['kind']:26} {text[:100]!r}")
        if l["trend"] and d.relevant:
            exp = EXPECTED_CATEGORY[l["trend"]]
            ok = d.category == exp
            cat_n += 1; cat_ok += ok
            t = per_trend.setdefault(l["trend"], [0, 0]); t[0] += ok; t[1] += 1
            if misses and not ok:
                print(f"  CAT {l['trend']:26} expected {exp:9} got {d.category!s:9} {text[:90]!r}")
    prec, rec, cat = tp / max(1, tp + fp), tp / max(1, tp + fn), cat_ok / max(1, cat_n)
    if quiet:
        return prec, rec, cat, fp, fn
    print(f"\n== {name}")
    print(f"relevance: precision {prec:.3f}  recall {rec:.3f}  (tp {tp} fp {fp} fn {fn} tn {tn})")
    print(f"category accuracy on relevant trend posts: {cat:.3f} ({cat_ok}/{cat_n})")
    for t, (ok, n) in sorted(per_trend.items()):
        print(f"  {t:28} {ok / n:.2f} ({ok}/{n})")
    print(f"decided by: {dict(methods)}")
    return prec, rec, cat, fp, fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", nargs="?", default=os.path.join(DEFAULT_DIR, "raw_content.jsonl"))
    ap.add_argument("labels", nargs="?", default=os.path.join(DEFAULT_DIR, "raw_content.labels.jsonl"))
    ap.add_argument("--threshold", type=float, default=0.5, help="keyword relevance threshold")
    ap.add_argument("--sure", type=float, default=float(os.environ.get("NORMALIZER_MODEL_SURE", "0.40")))
    ap.add_argument("--floor", type=float, default=float(os.environ.get("NORMALIZER_MODEL_FLOOR", "0.25")))
    ap.add_argument("--margin", type=float, default=float(os.environ.get("NORMALIZER_MODEL_MARGIN", "0.05")))
    ap.add_argument("--misses", action="store_true", help="print misclassified posts")
    ap.add_argument("--sweep", action="store_true", help="combined result across 'sure' thresholds")
    a = ap.parse_args()
    recs = [json.loads(line) for line in open(a.fixture, encoding="utf-8")]
    labs = [json.loads(line) for line in open(a.labels, encoding="utf-8")]
    texts = [clean_text(r["rawText"]) for r in recs]

    score("keywords", texts, labs, KeywordRelevance(a.threshold), a.misses)
    try:
        from normalizer.semantic import SemanticClassifier, load_minilm
        sc = SemanticClassifier(load_minilm())
    except Exception as exc:  # no model here: keywords only
        print(f"\n(model variants skipped: {exc.__class__.__name__}: {exc})")
        return
    results = sc.classify_many(texts)
    kw = dict(keyword_threshold=a.threshold, floor=a.floor, margin=a.margin)
    model_only = ModelRelevance(Cached(results), keyword_override=99, **kw, sure=a.sure)
    model_only.keyword_threshold = 99  # keywords never rescue anything: the model alone
    score(f"model (sure >= {a.sure})", texts, labs, model_only, a.misses)
    score(f"combined (sure >= {a.sure}, floor {a.floor}, margin {a.margin})", texts, labs,
          ModelRelevance(Cached(results), sure=a.sure, **kw), a.misses)
    if a.sweep:
        print("\n== combined, sweeping 'sure'\n  sure  precision  recall  category  fp  fn")
        for s in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55):
            p, r, c, fp, fn = score("", texts, labs, ModelRelevance(Cached(results), sure=s, **kw), quiet=True)
            print(f"  {s:.2f}  {p:9.3f}  {r:6.3f}  {c:8.3f}  {fp:2}  {fn:3}")


if __name__ == "__main__":
    main()
