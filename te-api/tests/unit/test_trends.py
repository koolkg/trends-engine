import unittest

from api.cache import FeedCache
from api.trends import (ApiError, LIST_FIELDS, TrendService, decode_cursor, encode_cursor, shape_detail,
                        shape_list_item)
from tests.fakes import T1, T2, T3, DictRedis, FakeRepo, payload, trend


def service(redis=None, repo=None, **kw):
    return TrendService(FeedCache(redis or DictRedis()), repo or FakeRepo(), **kw)


class TestCursor(unittest.TestCase):
    def test_round_trip_and_doc_example(self):
        for n in (0, 1, 20, 12345):
            self.assertEqual(decode_cursor(encode_cursor(n)), n)
        self.assertEqual(decode_cursor("eyJvZmZzZXQiOjIwfQ"), 20)  # the design doc's sample nextCursor
        self.assertEqual(encode_cursor(20), "eyJvZmZzZXQiOjIwfQ")

    def test_garbage_cursors_are_400(self):
        for bad in ("!!!", "e30", encode_cursor(0)[:-3] + "xyz", "eyJvZmZzZXQiOi0xfQ", "eyJvZmZzZXQiOiJ4In0"):
            with self.subTest(cursor=bad):
                with self.assertRaises(ApiError) as ctx:
                    decode_cursor(bad)
                self.assertEqual(ctx.exception.status, 400)


class TestShaping(unittest.TestCase):
    def test_list_item_has_doc_fields_plus_extras_only(self):
        item = shape_list_item(trend(T1, 1, "#lowercase", "social"))
        self.assertTrue(set(LIST_FIELDS) <= set(item))
        self.assertEqual(set(item) - set(LIST_FIELDS), {"rank", "velocityLabel", "summary"})
        self.assertNotIn("samplePosts", item)  # detail-only fields stay out of the list
        self.assertEqual((item["trendId"], item["sparkline"], item["sources"]), (T1, [0, 1, 4, 9], ["x", "tiktok"]))

    def test_detail_shape(self):
        d = shape_detail(trend(T1, 1, "#lowercase", "social"))
        for k in ("trendId", "clusterId", "relatedTerms", "samplePosts", "growth"):
            self.assertIn(k, d)
        self.assertEqual(d["samplePosts"][0], {"text": "brands posting lowercase", "source": "x",
                                               "postedAt": "2026-09-07T19:10:00Z", "author": "@a"})
        self.assertNotIn("signalKey", d)  # internal bookkeeping isn't exposed
        self.assertEqual(d["highlights"], ["#lowercase jumped to 9 posts in the 7 PM UTC hour (usually fewer than 1 an hour)."])
        self.assertIsNone(shape_detail({"trendId": T1}).get("highlights"))  # rankings from before highlights existed


class TestList(unittest.TestCase):
    def setUp(self):
        self.redis = DictRedis()
        self.redis.seed(payload())
        self.repo = FakeRepo(payload("db-version"))
        self.svc = service(self.redis, self.repo, default_limit=2)

    def test_cache_hit_pages_with_cursor_and_never_touches_postgres(self):
        page1, feed = self.svc.list_trends()
        self.assertEqual(([i["trendId"] for i in page1["items"]], feed.source), ([T1, T2], "cache"))
        page2, _ = self.svc.list_trends(cursor=page1["nextCursor"])
        self.assertEqual(([i["trendId"] for i in page2["items"]], page2["nextCursor"]), ([T3], None))
        self.assertEqual(self.repo.calls, 0)

    def test_category_filter(self):
        body, _ = self.svc.list_trends(category="email")
        self.assertEqual([i["trendId"] for i in body["items"]], [T2])

    def test_validation(self):
        for kwargs in ({"category": "sports"}, {"limit": "zero"}, {"limit": "0"}, {"limit": "101"},
                       {"cursor": "nope"}):
            with self.subTest(**kwargs):
                with self.assertRaises(ApiError) as ctx:
                    self.svc.list_trends(**kwargs)
                self.assertEqual(ctx.exception.status, 400)

    def test_offset_past_the_end_is_an_empty_page(self):
        body, _ = self.svc.list_trends(cursor="eyJvZmZzZXQiOjIwfQ")
        self.assertEqual(body, {"items": [], "nextCursor": None})


class TestCacheMissFallback(unittest.TestCase):
    def test_miss_reads_postgres_and_repopulates(self):
        redis, repo = DictRedis(), FakeRepo(payload("v-db"))
        svc = service(redis, repo)
        body, feed = svc.list_trends()
        self.assertEqual((feed.source, len(body["items"]), repo.calls), ("database", 3, 1))
        self.assertEqual(redis.data["trends:current"], "v-db")
        _, feed2 = svc.list_trends()  # now served from the repopulated cache
        self.assertEqual((feed2.source, repo.calls), ("cache", 1))

    def test_expired_version_behind_the_pointer_counts_as_a_miss(self):
        redis = DictRedis()
        redis.data["trends:current"] = "v-expired"   # pointer survives, its version expired
        _, feed = service(redis, FakeRepo(payload("v-db"))).list_trends()
        self.assertEqual(feed.source, "database")
        self.assertEqual(redis.data["trends:current"], "v-db")

    def test_redis_down_still_serves_from_postgres(self):
        body, feed = service(DictRedis(down=True), FakeRepo(payload())).list_trends()
        self.assertEqual((feed.source, len(body["items"])), ("database", 3))

    def test_nothing_ranked_yet(self):
        body, feed = service(DictRedis(), FakeRepo(None)).list_trends()
        self.assertEqual((body, feed), ({"items": [], "nextCursor": None}, None))


class TestDetail(unittest.TestCase):
    def test_found_from_cache_and_from_fallback(self):
        redis = DictRedis()
        redis.seed(payload())
        d, feed = service(redis).get_trend(T2)
        self.assertEqual((d["trendId"], d["name"], feed.source), (T2, "#emailmarketing", "cache"))
        d, feed = service(DictRedis(), FakeRepo(payload())).get_trend(T3)
        self.assertEqual((d["trendId"], feed.source), (T3, "database"))

    def test_missing_aged_out_or_malformed_is_404(self):
        redis = DictRedis()
        redis.seed(payload())
        for tid in ("00000000-0000-0000-0000-000000000099", "not-a-uuid", "../../etc/passwd", T1 + "0"):
            with self.subTest(tid=tid):
                with self.assertRaises(ApiError) as ctx:
                    service(redis).get_trend(tid)
                self.assertEqual(ctx.exception.status, 404)


    def test_ids_are_case_insensitive(self):
        redis = DictRedis()
        redis.seed(payload())
        self.assertNotEqual(T1, T1.upper())
        self.assertEqual(service(redis).get_trend(T1.upper())[0]["trendId"], T1)


class TestFeedCache(unittest.TestCase):
    def test_repopulate_does_not_clobber_a_newer_live_pointer(self):
        redis = DictRedis()
        redis.seed(payload("v-newer"))                 # te-aggregator published meanwhile
        FeedCache(redis).repopulate(payload("v-older"))
        self.assertEqual(redis.data["trends:current"], "v-newer")
        self.assertIn("trends:v:v-older", redis.data)  # harmless; expires on its TTL
        self.assertEqual(redis.ttl["trends:v:v-older"], 3600)

    def test_prefix_isolates_keys(self):
        redis = DictRedis()
        redis.seed(payload("v1"), prefix="other")
        self.assertIsNone(FeedCache(redis, prefix="trends").read())
        self.assertEqual(FeedCache(redis, prefix="other").read()["version"], "v1")


if __name__ == "__main__":
    unittest.main()
