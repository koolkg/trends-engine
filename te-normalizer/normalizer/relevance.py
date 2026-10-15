"""NORM-4: is this post about marketing, and which category? (decision NORM-9)

Two classifiers, one decision:
  * The model (semantic.py) is primary: it judges by meaning, so it copes
    with new slang and wording the keyword table has never seen.
  * The keyword table (classifier.py) is the backup and the validator:
      - backup: if the model can't load or fails on a post, keywords decide;
      - validator: when the model is unsure, the post is kept only if the
        keywords agree; and when the model says "not marketing" but the
        keywords are strongly sure it is, the keywords win.

Zones, by the model's similarity to its closest marketing example and the
margin over its closest off-topic example:

    sure    similarity >= sure and margin >= margin       -> model decides: keep
    no      similarity <  floor or margin <= -margin      -> drop, unless keywords are strongly sure
    unsure  anything in between                            -> keep only if keywords say relevant

`method` on every decision records which path decided, so the eval script
and logs can show how often each path is used and where the two disagree.
"""
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from normalizer.classifier import classify as keyword_classify, is_relevant

logger = logging.getLogger(__name__)

MODEL, KEYWORDS_CHECK, KEYWORDS_OVERRIDE, KEYWORDS_ONLY, KEYWORDS_FALLBACK = (
    "model", "keywords-check", "keywords-override", "keywords", "keywords-fallback")


@dataclass(frozen=True)
class Decision:
    relevant: bool
    category: Optional[str]
    relevance_score: float   # 0.000 - 1.000 (matches NUMERIC(4,3))
    method: str


def _clip(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 3)


class KeywordRelevance:
    """Keyword table only: the pre-model behaviour, and the backup when the model is unavailable."""

    def __init__(self, threshold: float, method: str = KEYWORDS_ONLY):
        self.threshold, self.method = threshold, method
        self.counts = Counter()

    def decide(self, text: str) -> Decision:
        kw = keyword_classify(text)
        relevant = is_relevant(kw, self.threshold)
        self.counts[self.method] += 1
        return Decision(relevant, kw.category, kw.relevance_score, self.method)


class ModelRelevance:
    def __init__(self, semantic, keyword_threshold: float = 0.5, sure: float = 0.40, floor: float = 0.25,
                 margin: float = 0.05, keyword_override: float = 1.0):
        self.semantic = semantic
        self.keyword_threshold, self.sure, self.floor, self.margin = keyword_threshold, sure, floor, margin
        self.keyword_override = keyword_override
        self.counts = Counter()       # decisions by method
        self.disagreements = Counter()  # model sure one way, keywords the other

    def decide(self, text: str) -> Decision:
        kw = keyword_classify(text)
        kw_relevant = is_relevant(kw, self.keyword_threshold)
        try:
            sem = self.semantic.classify(text)
        except Exception:  # model failure on one post must not stop the pipeline
            logger.exception("semantic classifier failed; using keywords for this post")
            self.counts[KEYWORDS_FALLBACK] += 1
            return Decision(kw_relevant, kw.category, kw.relevance_score, KEYWORDS_FALLBACK)

        if sem.similarity >= self.sure and sem.margin >= self.margin:
            if not kw_relevant:
                self.disagreements["model-yes-keywords-no"] += 1
            d = Decision(True, sem.category, _clip(sem.similarity), MODEL)
        elif sem.similarity < self.floor or sem.margin <= -self.margin:
            if kw.relevance_score >= self.keyword_override and kw.category:
                self.disagreements["model-no-keywords-yes"] += 1
                d = Decision(True, kw.category, kw.relevance_score, KEYWORDS_OVERRIDE)
            else:
                d = Decision(False, sem.category, _clip(sem.similarity), MODEL)
        else:
            if kw_relevant:
                d = Decision(True, kw.category or sem.category, kw.relevance_score, KEYWORDS_CHECK)
            else:
                d = Decision(False, sem.category, _clip(sem.similarity), KEYWORDS_CHECK)
        self.counts[d.method] += 1
        return d


def build(config) -> object:
    """The classifier the service runs with. Falls back to keywords only if
    the model is switched off or can't be loaded, and says so in the log."""
    if config.classifier != "model":
        logger.info("relevance: keyword table only (NORMALIZER_CLASSIFIER=%s)", config.classifier)
        return KeywordRelevance(config.relevance_threshold)
    try:
        from normalizer.semantic import SemanticClassifier, load_minilm
        semantic = SemanticClassifier(load_minilm())
    except Exception:
        logger.exception("relevance: model unavailable; falling back to the keyword table")
        return KeywordRelevance(config.relevance_threshold, method=KEYWORDS_FALLBACK)
    return ModelRelevance(semantic, keyword_threshold=config.relevance_threshold, sure=config.model_sure,
                          floor=config.model_floor, margin=config.model_margin,
                          keyword_override=config.keyword_override)
