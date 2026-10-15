"""Scenario 2: flush the Redis key by hand, confirm GET /v1/trends
still returns the same data via te-api's Postgres fallback, and that the
fallback repopulated the cache."""
import unittest

from e2e.harness import CURRENT_KEY, api, redis, replay_fixture_once


class CacheMissFallback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        replay_fixture_once()

    def test_flushed_cache_falls_back_to_postgres_with_identical_data(self):
        status, h, cached = api("/v1/trends?limit=100")
        self.assertEqual((status, h.get("X-Trends-Source")), (200, "cache"))

        r = redis()
        try:
            self.assertEqual(r.delete(CURRENT_KEY), 1)
        finally:
            r.close()

        status, h, fallback = api("/v1/trends?limit=100")
        self.assertEqual((status, h.get("X-Trends-Source")), (200, "database"))
        self.assertEqual(fallback, cached)  # same ranking, same order, same fields

        status, h, again = api("/v1/trends?limit=100")
        self.assertEqual((status, h.get("X-Trends-Source")), (200, "cache"))  # repopulated
        self.assertEqual(again, cached)

    def test_detail_survives_a_flush_too(self):
        _, _, feed = api("/v1/trends?limit=1")
        tid = feed["items"][0]["trendId"]
        r = redis()
        try:
            r.delete(CURRENT_KEY)
        finally:
            r.close()
        status, h, d = api(f"/v1/trends/{tid}")
        self.assertEqual((status, h.get("X-Trends-Source"), d["trendId"]), (200, "database", tid))


if __name__ == "__main__":
    unittest.main()
