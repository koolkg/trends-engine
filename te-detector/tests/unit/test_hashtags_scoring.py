import math
import unittest

from detector.config import parse_tags
from detector.hashtags import extract_hashtags
from detector.scoring import FALLING, RISING, SPIKE, STEADY, Baseline, velocity_label


class TestHashtags(unittest.TestCase):
    def test_extracts_lowercased_distinct_tags(self):
        self.assertEqual(extract_hashtags("Brands in #Lowercase now #lowercase #BrandVoice"),
                         {"#lowercase", "#brandvoice"})

    def test_ignores_listed_numeric_and_single_char_tags(self):
        ignore = parse_tags("#fyp, Marketing")
        self.assertEqual(extract_hashtags("#fyp #marketing #2026 #a #dupe", ignore), {"#dupe"})

    def test_not_fooled_by_urls_or_double_hash(self):
        self.assertEqual(extract_hashtags("see page#section and ##double and mid#word"), set())

    def test_no_tags(self):
        self.assertEqual(extract_hashtags("no tags here"), set())


class TestBaseline(unittest.TestCase):
    def test_ewma_update_matches_hand_computed_values(self):
        b = Baseline(alpha=0.5)
        b.update(4)   # delta 4: mean 2.0, var 0.5*(0 + 0.5*16) = 4.0
        self.assertAlmostEqual(b.mean, 2.0)
        self.assertAlmostEqual(b.var, 4.0)
        b.update(0)   # delta -2: mean 1.0, var 0.5*(4 + 0.5*4) = 3.0
        self.assertAlmostEqual(b.mean, 1.0)
        self.assertAlmostEqual(b.var, 3.0)

    def test_z_score_formula(self):
        b = Baseline(alpha=0.3, mean=4.0, var=5.0)
        self.assertAlmostEqual(b.z(10), (10 - 4) / math.sqrt(5 + 4 + 1))

    def test_poisson_floor_damps_tiny_counts(self):
        self.assertAlmostEqual(Baseline(alpha=0.3).z(2), 2.0)  # 0 -> 2 is z=2, not infinity

    def test_negligible_after_long_silence(self):
        b = Baseline(alpha=0.3)
        b.update(5)
        self.assertFalse(b.negligible)
        for _ in range(40):
            b.update(0)
        self.assertTrue(b.negligible)


class TestVelocityLabel(unittest.TestCase):
    def test_spike_fires_exactly_at_threshold_not_before(self):
        self.assertEqual(velocity_label(3.0, 3.0), SPIKE)
        self.assertEqual(velocity_label(2.999, 3.0), RISING)

    def test_other_bands(self):
        self.assertEqual(velocity_label(1.0, 3.0), RISING)
        self.assertEqual(velocity_label(0.99, 3.0), STEADY)
        self.assertEqual(velocity_label(-0.99, 3.0), STEADY)
        self.assertEqual(velocity_label(-1.0, 3.0), FALLING)


if __name__ == "__main__":
    unittest.main()
