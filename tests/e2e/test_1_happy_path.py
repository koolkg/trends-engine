"""Scenario 1: the fixture flows connector -> normalizer ->
detector/cluster -> aggregator, and GET /v1/trends returns the trends.
Then POST /v1/campaigns/draft-block puts the top trend into a draft Mailchimp
campaign (te-api -> te-infra's fake Mailchimp API) and records the block."""
import unittest

from e2e.harness import API_URL, PLATFORMS, TREND_TAGS, api, http, mailchimp, query, replay_fixture_once


class HappyPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replay = replay_fixture_once()
        status, cls.headers, cls.body = api("/v1/trends?limit=100")
        assert status == 200, (status, cls.body)
        cls.items = cls.body["items"]

    def test_feed_has_ranked_trends_in_the_documented_shape(self):
        self.assertGreaterEqual(len(self.items), 3, self.items)
        self.assertEqual([i["rank"] for i in self.items], sorted(i["rank"] for i in self.items))
        for item in self.items:
            for field in ("trendId", "name", "category", "velocityScore", "deltaPct", "firstSeenAt", "sources",
                          "sparkline"):
                self.assertIn(field, item)
            self.assertIn(item["category"], {"email", "social", "ecommerce", "content"})
            self.assertTrue(set(item["sources"]) <= PLATFORMS, item["sources"])

    def test_the_fixtures_trends_are_found(self):
        names = " ".join(i["name"] for i in self.items)
        found = {t for t in TREND_TAGS if t in names}
        self.assertGreaterEqual(len(found), 2, f"expected known trend hashtags in {names!r}")
        self.assertTrue({"#lowercase", "#brandvoice"} & found, "the fixture's sharpest spike should rank")

    def test_trends_span_platforms(self):
        multi = [i for i in self.items if len(i["sources"]) >= 2]
        self.assertTrue(multi, "cross-platform trends should be detected across more than one platform")

    def test_detail_of_the_top_trend(self):
        top = self.items[0]
        status, _, d = api(f"/v1/trends/{top['trendId']}")
        self.assertEqual(status, 200)
        self.assertEqual((d["trendId"], d["name"]), (top["trendId"], top["name"]))
        self.assertTrue(d["samplePosts"])
        fixture_texts = {p["rawText"].split()[0].lower() for p in self.replay["posts"]}
        for p in d["samplePosts"]:
            self.assertIn(p["source"], PLATFORMS)
            self.assertIn(p["text"].split()[0].lower(), fixture_texts)  # samples are real fixture posts
        self.assertTrue(d["growth"])
        # "Why it's trending": plain words for the dashboard; the raw scores stay in `signals`
        self.assertTrue(d["highlights"], d)
        self.assertFalse([h for h in d["highlights"] if "z-score" in h or "zScore" in h])

    def test_insert_into_campaign(self):
        top = self.items[0]
        request = {"trendId": top["trendId"], "campaignId": None, "tone": "casual"}
        status, _, preview = http("POST", API_URL + "/v1/campaigns/draft-block/preview", request)
        self.assertEqual(status, 200, preview)
        status, _, body = http("POST", API_URL + "/v1/campaigns/draft-block", request)
        self.assertEqual(status, 201, body)
        self.assertEqual((body["subjectLine"], body["suggestedCopy"]), (preview["subjectLine"], preview["suggestedCopy"]))
        self.assertEqual(body["status"], "draft")
        self.assertTrue(body["blockId"] and body["suggestedCopy"])
        # The draft really is in (fake) Mailchimp, holding the suggested copy...
        campaign = mailchimp(f"/campaigns/{body['campaignId']}")
        self.assertEqual((campaign["status"], campaign["type"]), ("save", "regular"))
        self.assertEqual(campaign["settings"]["subject_line"], body["subjectLine"])
        self.assertIn(body["suggestedCopy"][:40], mailchimp(f"/campaigns/{body['campaignId']}/content")["plain_text"])
        # ...and recorded against the trend.
        row = query("SELECT trend_id::text, mailchimp_campaign_id, status FROM campaign_draft_block WHERE id = %s",
                    (body["blockId"],))
        self.assertEqual(row, [(top["trendId"], body["campaignId"], "draft")])


if __name__ == "__main__":
    unittest.main()
