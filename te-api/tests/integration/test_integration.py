"""te-api IT against the real Postgres, Redis and te-infra's mailchimp-fake.

The check: "hit both endpoints and check the response; delete the Redis key
and confirm the fallback still returns the right data from Postgres."

Self-contained and non-destructive:
- Postgres: inserts one ranked trend_ranking row (rank 9999, so it sorts
  after any real ranking) shaped like te-aggregator's output, and deletes it
  afterwards.
- Redis: the server under test uses its own key prefix (it:<run>), so
  deleting "the" cache key here never touches the live feed.
- Campaigns: draft blocks created here are deleted afterwards; the fake
  Mailchimp keeps its campaigns in memory only.
The real HTTP server runs in-process on an ephemeral port.
"""
import base64
import http.client
import json
import urllib.request
import threading
import unittest
import uuid
from datetime import datetime, timezone

import psycopg2

from api.cache import FeedCache
from api.campaigns import CampaignService
from api.mailchimp import MailchimpClient
from api.config import load_config
from api.redis_client import Redis
from api.repository import Repository
from api.server import make_server
from api.trends import TrendService

CFG = load_config()


class ApiIT(unittest.TestCase):
    def setUp(self):
        self.run_id = uuid.uuid4().hex[:8]
        self.prefix = f"it:{self.run_id}"
        self.trend_id = str(uuid.uuid4())
        self.redis = Redis(CFG.redis_host, CFG.redis_port)
        self.db = psycopg2.connect(host=CFG.postgres_host, port=CFG.postgres_port, user=CFG.postgres_user,
                                   password=CFG.postgres_password, dbname=CFG.postgres_db)
        detail = {
            "trendId": self.trend_id, "rank": 9999, "name": f"#it{self.run_id}", "summary": "integration test trend",
            "category": "social", "velocityScore": 4.2, "velocityLabel": "rising", "deltaPct": 120,
            "firstSeenAt": "2026-09-07T10:00:00Z", "sources": ["x"], "sparkline": [1, 2, 3], "clusterId": None,
            "relatedTerms": [], "samplePosts": [{"text": "hello", "source": "x", "postedAt": "2026-09-07T10:00:00Z",
                                                 "author": "@it"}],
            "growth": [{"date": "2026-09-07T10:00:00Z", "score": 3}], "signals": [], "signalKey": f"term:#it{self.run_id}",
        }
        with self.db, self.db.cursor() as cur:
            cur.execute("INSERT INTO trend_ranking (id, label, category, velocity_score, rank, computed_at, detail,"
                        " signal_key) VALUES (%s, %s, 'social', 4.2, 9999, %s, %s::jsonb, %s)",
                        (self.trend_id, detail["name"], datetime.now(timezone.utc), json.dumps(detail),
                         detail["signalKey"]))

        def service():
            return TrendService(FeedCache(Redis(CFG.redis_host, CFG.redis_port), self.prefix, 60),
                                Repository(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user,
                                           CFG.postgres_password, CFG.postgres_db), max_limit=CFG.max_limit)
        def campaigns():
            return CampaignService(Repository(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user,
                                              CFG.postgres_password, CFG.postgres_db),
                                   MailchimpClient(CFG.mailchimp_api_url, CFG.mailchimp_api_key))
        self.server = make_server("127.0.0.1", 0, service, campaigns)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        with self.db, self.db.cursor() as cur:
            cur.execute("DELETE FROM campaign_draft_block WHERE trend_id = %s", (self.trend_id,))
            cur.execute("DELETE FROM trend_ranking WHERE id = %s", (self.trend_id,))
        current = self.redis.get(f"{self.prefix}:current")
        keys = [f"{self.prefix}:current"] + ([f"{self.prefix}:v:{current.decode()}"] if current else [])
        self.redis.delete(*keys)
        self.redis.close()
        self.db.close()

    def get(self, path, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                     headers={"Content-Type": "application/json"} if body is not None else {})
        r = conn.getresponse()
        body = json.loads(r.read())
        conn.close()
        return r, body

    def all_items(self):
        items, cursor = [], None
        while True:
            r, body = self.get("/v1/trends?limit=100" + (f"&cursor={cursor}" if cursor else ""))
            self.assertEqual(r.status, 200)
            items += body["items"]
            cursor = body["nextCursor"]
            if not cursor:
                return items, r

    def test_cache_miss_falls_back_to_postgres_then_serves_from_cache(self):
        items, r = self.all_items()  # empty prefix: guaranteed miss on the first request
        self.assertEqual(r.getheader("X-Trends-Source"), "database")
        self.assertIn(self.trend_id, [i["trendId"] for i in items])
        self.assertIsNotNone(self.redis.get(f"{self.prefix}:current"))  # repopulated

        r, body = self.get(f"/v1/trends/{self.trend_id}")
        self.assertEqual((r.status, r.getheader("X-Trends-Source")), (200, "cache"))
        self.assertEqual((body["name"], body["samplePosts"][0]["text"]), (f"#it{self.run_id}", "hello"))

        self.redis.delete(f"{self.prefix}:current")  # flush the cache, as the plan's IT does
        r, body = self.get(f"/v1/trends/{self.trend_id}")
        self.assertEqual((r.status, r.getheader("X-Trends-Source")), (200, "database"))
        self.assertEqual(body["trendId"], self.trend_id)

    def test_aged_out_trend_is_404(self):
        with self.db, self.db.cursor() as cur:
            cur.execute("UPDATE trend_ranking SET rank = NULL WHERE id = %s", (self.trend_id,))
        r, body = self.get(f"/v1/trends/{self.trend_id}")
        self.assertEqual((r.status, body["error"]), (404, "not_found"))


    def mailchimp(self, path):
        req = urllib.request.Request(CFG.mailchimp_api_url + path)
        req.add_header("Authorization", "Basic " + base64.b64encode(f"it:{CFG.mailchimp_api_key}".encode()).decode())
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_draft_block_creates_a_mailchimp_draft_and_records_it(self):
        r, preview = self.get("/v1/campaigns/draft-block/preview", "POST",
                              {"trendId": self.trend_id, "campaignId": None, "tone": "professional"})
        self.assertEqual(r.status, 200, preview)
        r, body = self.get("/v1/campaigns/draft-block", "POST",
                           {"trendId": self.trend_id, "campaignId": None, "tone": "professional"})
        self.assertEqual((r.status, body["status"]), (201, "draft"), body)
        for k in ("subjectLine", "previewText", "headline", "suggestedCopy"):  # preview == what went in
            self.assertEqual(preview[k], body[k], k)
        self.assertTrue(body["previewUrl"].endswith(f"/archive/{body['campaignId']}"))
        campaign = self.mailchimp(f"/campaigns/{body['campaignId']}")
        self.assertEqual((campaign["status"], campaign["settings"]["subject_line"]), ("save", body["subjectLine"]))
        content = self.mailchimp(f"/campaigns/{body['campaignId']}/content")
        self.assertIn(f"it{self.run_id}", content["html"])
        self.assertNotIn("<a ", content["html"])  # no button: the marketer adds their own in Mailchimp
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT trend_id::text, mailchimp_campaign_id, suggested_copy, status"
                        " FROM campaign_draft_block WHERE id = %s", (body["blockId"],))
            self.assertEqual(cur.fetchone(), (self.trend_id, body["campaignId"], body["suggestedCopy"], "draft"))

        # A second block into the same (now existing) campaign
        r, again = self.get("/v1/campaigns/draft-block", "POST",
                            {"trendId": self.trend_id, "campaignId": body["campaignId"], "tone": "expert"})
        self.assertEqual((r.status, again["campaignId"]), (201, body["campaignId"]))
        r, missing = self.get("/v1/campaigns/draft-block", "POST",
                              {"trendId": self.trend_id, "campaignId": "0000000000"})
        self.assertEqual((r.status, missing["error"]), (404, "campaign_not_found"))


if __name__ == "__main__":
    unittest.main()
