"""te-api over the live ranking: pagination walks the whole feed exactly once,
filters agree with the unfiltered feed, every listed trend has a detail
page, and errors are well-formed."""
import unittest

from e2e.harness import api, replay_fixture_once

CATEGORIES = ("email", "social", "ecommerce", "content")


class ApiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        replay_fixture_once()
        _, _, body = api("/v1/trends?limit=100")
        cls.all = body["items"]

    def test_pagination_walks_the_feed_exactly_once(self):
        seen, cursor = [], None
        while True:
            status, _, body = api("/v1/trends?limit=2" + (f"&cursor={cursor}" if cursor else ""))
            self.assertEqual(status, 200)
            seen += [i["trendId"] for i in body["items"]]
            cursor = body["nextCursor"]
            if not cursor:
                break
        self.assertEqual(seen, [i["trendId"] for i in self.all])

    def test_category_filters_partition_the_feed(self):
        union = []
        for cat in CATEGORIES:
            status, _, body = api(f"/v1/trends?limit=100&category={cat}")
            self.assertEqual(status, 200)
            self.assertTrue(all(i["category"] == cat for i in body["items"]))
            union += [i["trendId"] for i in body["items"]]
        self.assertEqual(sorted(union), sorted(i["trendId"] for i in self.all))

    def test_every_listed_trend_has_a_matching_detail(self):
        for item in self.all:
            status, _, d = api(f"/v1/trends/{item['trendId']}")
            self.assertEqual(status, 200, item["name"])
            for k in ("trendId", "name", "category", "velocityScore", "sparkline"):
                self.assertEqual(d[k], item[k])

    def test_errors(self):
        for path, status in (("/v1/trends/00000000-0000-0000-0000-000000000000", 404), ("/v1/trends/nope", 404),
                             ("/v1/trends?limit=0", 400), ("/v1/trends?category=sports", 400),
                             ("/v1/trends?cursor=garbage", 400), ("/v1/nope", 404)):
            with self.subTest(path=path):
                got, _, body = api(path)
                self.assertEqual(got, status)
                self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
