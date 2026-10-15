"""The real all-MiniLM-L6-v2 behind relevance + category: only meaningful
where the model is installed (the container image bakes it in). Skipped
elsewhere. These are sanity checks on clear-cut posts; accuracy on the whole
fixture is measured by `make eval`."""
import unittest

try:
    import sentence_transformers  # noqa: F401
    HAVE_MODEL = True
except ImportError:
    HAVE_MODEL = False


@unittest.skipUnless(HAVE_MODEL, "sentence-transformers not installed (runs in the container)")
class TestRealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from normalizer.semantic import SemanticClassifier, load_minilm
        cls.sc = SemanticClassifier(load_minilm())

    def test_clear_categories(self):
        cases = {
            "tested numbers vs emojis in our subject lines, open rate jumped": "email",
            "found a $12 dupe for the $58 serum, it's sold out everywhere": "ecommerce",
            "our reels get way more comments since we started replying in the brand voice": "social",
        }
        for text, cat in cases.items():
            self.assertEqual(self.sc.classify(text).category, cat, text)

    def test_marketing_without_any_keyword_still_looks_like_marketing(self):
        r = self.sc.classify("every company account I follow is typing like a friend texting now")
        self.assertGreater(r.margin, 0)

    def test_everyday_chatter_looks_off_topic(self):
        for text in ("made sourdough for the first time and it's a brick", "learning to knit, wish me luck",
                     "concert tickets sold out in 4 minutes, of course"):   # look-alike that slipped through e2e once
            self.assertLess(self.sc.classify(text).margin, 0, text)


if __name__ == "__main__":
    unittest.main()
