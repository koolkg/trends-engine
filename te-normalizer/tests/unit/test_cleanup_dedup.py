import unittest

from normalizer.cleanup import clean_text
from normalizer.dedup import dedup_hash, dedup_key


class TestCleanText(unittest.TestCase):
    def test_strips_urls_markup_entities_and_collapses_whitespace(self):
        raw = "<b>found</b> the &amp; dupe\n\n see https://x.com/a/status/1 and www.shop.example/p  now"
        self.assertEqual(clean_text(raw), "found the & dupe see and now")

    def test_removes_control_and_zero_width_characters(self):
        self.assertEqual(clean_text("zero​width\tand nbsp﻿"), "zero width and nbsp")

    def test_keeps_hashtags_mentions_and_emoji(self):
        self.assertEqual(clean_text("POV: unboxing 📦 @brand #POV #ugc"), "POV: unboxing 📦 @brand #POV #ugc")

    def test_nfkc_normalizes_lookalike_characters(self):
        self.assertEqual(clean_text("ｆｕｌｌｗｉｄｔｈ"), "fullwidth")


class TestDedup(unittest.TestCase):
    def test_same_text_different_decoration_hashes_the_same(self):
        a = "Found the $12 dupe for the $58 serum!! 🔥\n\n#dupe #budgetbeauty"
        b = "found the $12 dupe for the $58 serum #fyp @someone"
        self.assertEqual(dedup_key(clean_text(a)), dedup_key(clean_text(b)))
        self.assertEqual(dedup_hash(clean_text(a)), dedup_hash(clean_text(b)))

    def test_different_prices_are_different_posts(self):
        self.assertNotEqual(dedup_hash("found the $12 dupe for the $58 serum"),
                            dedup_hash("found the $9 dupe for the $45 serum"))

    def test_shared_wording_with_extra_words_is_not_a_duplicate(self):
        # echoing a phrase is the trend signal, not a duplicate
        self.assertNotEqual(dedup_hash("POV framing is quietly replacing the classic unboxing video"),
                            dedup_hash("honestly, POV framing is quietly replacing the classic unboxing video"))

    def test_tag_only_post_falls_back_to_full_text(self):
        self.assertTrue(dedup_key("#dupe #fyp"))
        self.assertNotEqual(dedup_hash("#dupe #fyp"), dedup_hash("#pov #ugc"))

    def test_hash_is_sha256_hex(self):
        self.assertRegex(dedup_hash("anything"), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
