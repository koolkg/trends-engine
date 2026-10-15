"""The real stdlib HTTP server on an ephemeral port, with in-memory backends."""
import http.client
import json
import threading
import unittest

from api.cache import FeedCache
from api.server import make_server, route
from api.trends import TrendService
from api.campaigns import CampaignService
from tests.fakes import T1, DictRedis, FakeMailchimp, FakeRepo, payload


class Boom:
    def read(self):
        raise RuntimeError("unexpected")


class TestRoute(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(route("/v1/trends"), ("list", None))
        self.assertEqual(route("/v1/trends/"), ("list", None))
        self.assertEqual(route("/v1/trends/abc"), ("detail", "abc"))
        self.assertEqual(route("/healthz"), ("health", None))
        self.assertEqual(route("/v1/campaigns/draft-block"), ("draft-block", None))
        self.assertEqual(route("/v1/campaigns/draft-block/preview"), ("draft-preview", None))
        self.assertEqual(route("/v1/trends/a/b"), (None, None))
        self.assertEqual(route("/"), (None, None))


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redis = DictRedis()
        cls.redis.seed(payload())
        cls.repo = FakeRepo(payload())
        cls.server = make_server("127.0.0.1", 0, lambda: TrendService(FeedCache(cls.redis), FakeRepo()),
                                 lambda: CampaignService(cls.repo, FakeMailchimp()))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
        r = conn.getresponse()
        body = json.loads(r.read() or b"null")
        conn.close()
        return r, body

    def test_list(self):
        r, body = self.request("GET", "/v1/trends?limit=2&category=")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual((r.getheader("X-Trends-Source"), r.getheader("X-Trends-Version")),
                         ("cache", "20260907T200000000000Z"))
        self.assertEqual(len(body["items"]), 2)
        self.assertIsNotNone(body["nextCursor"])

    def test_detail_and_errors(self):
        r, body = self.request("GET", f"/v1/trends/{T1}")
        self.assertEqual((r.status, body["trendId"]), (200, T1))
        r, body = self.request("GET", "/v1/trends/00000000-0000-0000-0000-000000000099")
        self.assertEqual((r.status, body["error"]), (404, "not_found"))
        r, body = self.request("GET", "/v1/trends?limit=abc")
        self.assertEqual((r.status, body["error"]), (400, "bad_limit"))
        r, body = self.request("GET", "/nope")
        self.assertEqual(r.status, 404)
        r, body = self.request("POST", "/v1/trends")
        self.assertEqual(r.status, 405)
        r, body = self.request("GET", "/healthz")
        self.assertEqual((r.status, body), (200, {"status": "ok"}))

    def test_draft_block(self):
        r, body = self.request("POST", "/v1/campaigns/draft-block", {"trendId": T1, "campaignId": None, "tone": "playful"})
        self.assertEqual((r.status, body["status"]), (201, "draft"))
        self.assertTrue(body["blockId"] and body["suggestedCopy"] and body["campaignId"])
        self.assertIn(body["blockId"], [b[0] for b in self.repo.blocks])

    def test_draft_block_preview(self):
        r, body = self.request("POST", "/v1/campaigns/draft-block/preview", {"trendId": T1, "tone": "professional"})
        self.assertEqual((r.status, body["tone"]), (200, "professional"))
        self.assertTrue(body["subjectLine"] and body["suggestedCopy"] and body["headline"])
        r, _ = self.request("GET", "/v1/campaigns/draft-block/preview")
        self.assertEqual(r.status, 405)

    def test_draft_block_errors(self):
        r, body = self.request("POST", "/v1/campaigns/draft-block", raw=b"{not json")
        self.assertEqual((r.status, body["error"]), (400, "bad_json"))
        r, body = self.request("POST", "/v1/campaigns/draft-block", {"trendId": "00000000-0000-0000-0000-000000000099"})
        self.assertEqual((r.status, body["error"]), (404, "trend_not_found"))
        r, body = self.request("GET", "/v1/campaigns/draft-block")
        self.assertEqual(r.status, 405)
        r, body = self.request("POST", "/v1/nope", {})
        self.assertEqual(r.status, 404)
        r, body = self.request("PUT", "/v1/campaigns/draft-block", {})
        self.assertEqual(r.status, 405)
        r, body = self.request("POST", "/v1/campaigns/draft-block", raw=b"x" * (64 * 1024 + 1))
        self.assertEqual((r.status, body["error"]), (413, "too_large"))

    def test_unhandled_errors_are_500_without_leaking_details(self):
        server = make_server("127.0.0.1", 0, lambda: TrendService(Boom(), FakeRepo()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            with self.assertLogs("api.server", level="ERROR"):
                conn.request("GET", "/v1/trends")
                r = conn.getresponse()
                body = json.loads(r.read())
            self.assertEqual((r.status, body), (500, {"error": "internal", "message": "internal error"}))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
