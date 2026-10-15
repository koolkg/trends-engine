"""Scenario 4: off-topic posts are archived but never reach
normalized_content_item, content_cluster_assignment, term_frequency_snapshot
or the ranked feed. Also: exact reposts never get a second normalized row."""
import unittest

from e2e.harness import api, query, replay_fixture_once


class IrrelevantDropped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = replay_fixture_once()
        cls.noise_ids = [l["contentId"] for l in run["labels"] if l["kind"] == "noise"]
        cls.noise_texts = {p["contentId"]: p["rawText"] for p in run["posts"] if p["contentId"] in set(cls.noise_ids)}
        cls.exact_dups = [l["contentId"] for l in run["labels"] if l["duplicateKind"] == "exact"]
        assert cls.noise_ids and cls.exact_dups

    def test_noise_is_archived(self):
        n = query("SELECT count(DISTINCT external_content_id) FROM raw_content_item WHERE external_content_id = ANY(%s)",
                  (self.noise_ids,))[0][0]
        self.assertEqual(n, len(self.noise_ids))

    def test_noise_never_normalized_or_clustered(self):
        rows = query("SELECT r.external_content_id FROM normalized_content_item n"
                     " JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at"
                     " WHERE r.external_content_id = ANY(%s)", (self.noise_ids,))
        self.assertEqual(rows, [])
        rows = query("SELECT a.content_id FROM content_cluster_assignment a"
                     " JOIN normalized_content_item n ON n.id = a.content_id"
                     " JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at"
                     " WHERE r.external_content_id = ANY(%s)", (self.noise_ids,))
        self.assertEqual(rows, [])

    def test_noise_only_hashtags_never_scored(self):
        rows = query("SELECT DISTINCT term FROM term_frequency_snapshot WHERE term = ANY(%s)",
                     (["#life", "#weekend", "#mood"],))
        self.assertEqual(rows, [])

    def test_noise_never_appears_in_the_feed(self):
        _, _, feed = api("/v1/trends?limit=100")
        noise_texts = {t.split("\n")[0][:30].lower() for t in self.noise_texts.values()}
        for item in feed["items"]:
            _, _, d = api(f"/v1/trends/{item['trendId']}")
            for p in d["samplePosts"]:
                self.assertNotIn(p["text"][:30].lower(), noise_texts, f"noise post in {item['name']}")

    def test_exact_reposts_are_archived_but_not_normalized_twice(self):
        rows = query("SELECT r.external_content_id FROM normalized_content_item n"
                     " JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at"
                     " WHERE r.external_content_id = ANY(%s)", (self.exact_dups,))
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
