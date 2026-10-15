"""NORM-4 / NORM-9: the model decides by meaning; the keyword table is the
backup (model unavailable or failing) and the validator (model unsure, or
model says no while the keywords are strongly sure)."""
import json
import unittest

from normalizer import relevance as rel
from normalizer.pipeline import PUBLISHED, process
from normalizer.semantic import SemanticClassifier, SemanticResult
from tests.fake_embedder import FakeEmbedder
from tests.unit.test_pipeline import FakePublisher, FakeStore, msg

MARKETING = "our newsletter subject lines got 40% more opens"   # keywords: strongly email
NO_KEYWORDS = "everyone I follow writes like they're texting a friend now and it works"  # keywords: nothing
WEAK = "girl dinner tonight was crackers and a pickle"         # keywords: 0.25, below threshold


class StubSemantic:
    def __init__(self, category="social", similarity=0.5, off_topic=0.1, fail=False):
        self.result = SemanticResult(category, similarity, off_topic, {})
        self.fail = fail

    def classify(self, text):
        if self.fail:
            raise RuntimeError("model blew up")
        return self.result


def decide(text, **stub):
    m = rel.ModelRelevance(StubSemantic(**stub))
    return m, m.decide(text)


class Zones(unittest.TestCase):
    def test_model_sure_keeps_even_without_keywords(self):
        m, d = decide(NO_KEYWORDS, category="social", similarity=0.55, off_topic=0.1)
        self.assertEqual((d.relevant, d.category, d.method), (True, "social", rel.MODEL))
        self.assertEqual(d.relevance_score, 0.55)
        self.assertEqual(m.disagreements["model-yes-keywords-no"], 1)

    def test_model_sure_needs_a_margin_over_off_topic(self):
        _, d = decide(NO_KEYWORDS, similarity=0.45, off_topic=0.43)   # close to both: unsure
        self.assertEqual((d.relevant, d.method), (False, rel.KEYWORDS_CHECK))

    def test_unsure_and_keywords_agree_keeps_with_keyword_category(self):
        _, d = decide(MARKETING, category="content", similarity=0.32, off_topic=0.1)
        self.assertEqual((d.relevant, d.category, d.method), (True, "email", rel.KEYWORDS_CHECK))

    def test_unsure_and_keywords_disagree_drops(self):
        _, d = decide(WEAK, category="content", similarity=0.32, off_topic=0.1)
        self.assertEqual((d.relevant, d.method), (False, rel.KEYWORDS_CHECK))

    def test_model_no_is_overridden_only_by_strong_keywords(self):
        m, d = decide(MARKETING, similarity=0.1, off_topic=0.3)
        self.assertEqual((d.relevant, d.category, d.method), (True, "email", rel.KEYWORDS_OVERRIDE))
        self.assertEqual(m.disagreements["model-no-keywords-yes"], 1)
        _, d = decide(WEAK, similarity=0.1, off_topic=0.3)
        self.assertEqual((d.relevant, d.method), (False, rel.MODEL))

    def test_off_topic_clearly_closer_drops(self):
        _, d = decide(WEAK, similarity=0.35, off_topic=0.5)
        self.assertEqual((d.relevant, d.method), (False, rel.MODEL))

    def test_model_failure_falls_back_to_keywords(self):
        m, d = decide(MARKETING, fail=True)
        self.assertEqual((d.relevant, d.category, d.method), (True, "email", rel.KEYWORDS_FALLBACK))
        self.assertEqual(m.counts[rel.KEYWORDS_FALLBACK], 1)


class Semantic(unittest.TestCase):
    """The mechanics, with a bag-of-words stand-in for the model: a post goes
    to the category whose example sentences it shares the most with."""

    def setUp(self):
        self.sc = SemanticClassifier(FakeEmbedder(),
                                     prototypes={"email": ["newsletter subject lines and open rates"],
                                                 "ecommerce": ["cheap dupe of an expensive product"]},
                                     off_topic=["what I cooked for dinner tonight"])

    def test_nearest_category_wins(self):
        r = self.sc.classify("found a cheap dupe of my favourite product")
        self.assertEqual(r.category, "ecommerce")
        self.assertGreater(r.similarity, r.off_topic)

    def test_off_topic_is_measured_separately(self):
        r = self.sc.classify("what I cooked for dinner tonight")
        self.assertAlmostEqual(r.off_topic, 1.0, places=5)
        self.assertLess(r.margin, 0)

    def test_batch_matches_single(self):
        texts = ["newsletter open rates", "cheap dupe"]
        self.assertEqual([r.category for r in self.sc.classify_many(texts)],
                         [self.sc.classify(t).category for t in texts])


class Build(unittest.TestCase):
    class Cfg:
        classifier, relevance_threshold = "model", 0.5
        model_sure, model_floor, model_margin, keyword_override = 0.4, 0.25, 0.05, 1.0

    def test_keywords_only_when_switched_off(self):
        cfg = self.Cfg(); cfg.classifier = "keywords"
        r = rel.build(cfg)
        self.assertIsInstance(r, rel.KeywordRelevance)
        self.assertEqual(r.decide(MARKETING).method, rel.KEYWORDS_ONLY)

    def test_model_that_cannot_load_falls_back_to_keywords(self):
        import normalizer.semantic as sem
        real = sem.load_minilm
        sem.load_minilm = lambda: (_ for _ in ()).throw(OSError("no weights"))
        try:
            r = rel.build(self.Cfg())
        finally:
            sem.load_minilm = real
        self.assertEqual(r.decide(MARKETING).method, rel.KEYWORDS_FALLBACK)


class Pipeline(unittest.TestCase):
    def test_published_message_says_who_decided(self):
        store, pub = FakeStore(), FakePublisher()
        model = rel.ModelRelevance(StubSemantic(category="ecommerce", similarity=0.6, off_topic=0.1))
        self.assertEqual(process(msg(), store, pub, relevance=model), PUBLISHED)
        body = pub.published[0][1]
        self.assertEqual((body["category"], body["classifiedBy"], body["relevanceScore"]), ("ecommerce", "model", 0.6))

    def test_without_a_classifier_the_keyword_table_decides(self):
        store, pub = FakeStore(), FakePublisher()
        process(msg(), store, pub)
        self.assertEqual(pub.published[0][1]["classifiedBy"], rel.KEYWORDS_ONLY)


if __name__ == "__main__":
    unittest.main()
