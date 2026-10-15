import json
import os
import unittest

from normalizer.classifier import classify, is_relevant
from normalizer.cleanup import clean_text

THRESHOLD = 0.5
SLICE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "labeled_slice.jsonl")


class TestClassifierRules(unittest.TestCase):
    def test_off_topic_is_irrelevant(self):
        for text in ["just adopted a very good dog, no notes", "made sourdough for the first time",
                     "anyone watching the game tonight? #fyp #foryou", "lost my keys lol #life #mood",
                     "girl dinner tonight: crackers, a pickle and half a cheese stick #mood",
                     "POV: my dog sees the vacuum come out", "concert tickets sold out in 4 minutes"]:
            with self.subTest(text=text):
                self.assertFalse(is_relevant(classify(text), THRESHOLD))

    def test_categories(self):
        cases = {
            "found the $12 dupe for the $58 serum #dupe": "ecommerce",
            "numbers in subject lines lifted our open rate 19%": "email",
            "POV: unboxing video beat our demo on watch-through #ugc": "social",
            "our editorial calendar is scraps and the content format works #contentcreator": "content",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = classify(text)
                self.assertTrue(is_relevant(result, THRESHOLD))
                self.assertEqual(result.category, expected)

    def test_hashtags_count_as_keywords(self):
        self.assertGreater(classify("#emailmarketing").raw_scores["email"], 0)

    def test_hyphenated_keywords_match(self):
        self.assertGreater(classify("founder-voice openers").raw_scores["email"], 0)

    def test_score_is_bounded_and_three_decimals(self):
        result = classify("email newsletter subject line open rate preheader dupe ugc content copy")
        self.assertEqual(result.relevance_score, 1.0)
        self.assertEqual(classify("").relevance_score, 0.0)
        self.assertIsNone(classify("").category)


class TestLabeledFixtureSlice(unittest.TestCase):
    """The classifier on a small labeled slice of the CONN-1 fixture
    (first 5 non-duplicate posts of each trend/evergreen/noise, not hand-picked)."""

    @classmethod
    def setUpClass(cls):
        with open(SLICE, encoding="utf-8") as f:
            cls.rows = [json.loads(line) for line in f]

    def test_off_topic_is_always_dropped(self):
        # Hard requirement: off-topic posts (including look-alikes such as food
        # "girl dinner" or pet "POV" posts) must never reach the pipeline.
        for row in (r for r in self.rows if not r["relevant"]):
            with self.subTest(text=row["rawText"][:60]):
                self.assertFalse(is_relevant(classify(clean_text(row["rawText"])), THRESHOLD))

    def test_on_topic_kept_at_least_90_percent(self):
        # Realistic posts sometimes carry no marketing words at all; a keyword
        # rule can't catch those (a known PoC limitation of this classifier).
        on_topic = [r for r in self.rows if r["relevant"]]
        kept = sum(is_relevant(classify(clean_text(r["rawText"])), THRESHOLD) for r in on_topic)
        self.assertGreaterEqual(kept / len(on_topic), 0.9, f"{kept}/{len(on_topic)}")

    def test_category_accuracy_at_least_80_percent(self):
        scored = [r for r in self.rows if r["category"]]
        correct = sum(classify(clean_text(r["rawText"])).category == r["category"] for r in scored)
        self.assertGreaterEqual(correct / len(scored), 0.8, f"{correct}/{len(scored)}")


if __name__ == "__main__":
    unittest.main()
