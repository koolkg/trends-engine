"""POST /v1/campaigns/draft-block logic (campaigns.py), the copywriter and the
Mailchimp client, with in-memory fakes and a stub HTTP server."""
import base64
import html
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from api import copywriter
from api.campaigns import CampaignService, parse_request
from api.mailchimp import MailchimpClient, MailchimpError, MailchimpUnavailable
from api.trends import ApiError
from tests.fakes import T1, T2, FakeMailchimp, FakeRepo, payload


class TestRequest(unittest.TestCase):
    def test_defaults_and_normalization(self):
        self.assertEqual(parse_request({"trendId": T1.upper()}), (T1, None, "casual"))
        self.assertEqual(parse_request({"trendId": T1, "tone": "playful"})[2], "casual")  # the design doc's example value
        self.assertEqual(parse_request({"trendId": T1, "campaignId": "42694e9e57", "tone": "expert"}),
                         (T1, "42694e9e57", "expert"))

    def test_rejections(self):
        for body, code in [([], "bad_request"), ({}, "bad_trend_id"), ({"trendId": "t_9c21"}, "bad_trend_id"),
                           ({"trendId": T1, "campaignId": "../x"}, "bad_campaign_id"),
                           ({"trendId": T1, "campaignId": 7}, "bad_campaign_id"),
                           ({"trendId": T1, "tone": "angry"}, "bad_tone")]:
            with self.subTest(body=body), self.assertRaises(ApiError) as e:
                parse_request(body)
            self.assertEqual((e.exception.status, e.exception.code), (400, code))


class TestCopywriter(unittest.TestCase):
    TREND = {"name": "#lowercase / #brandvoice", "deltaPct": 984, "sources": ["x", "tiktok", "instagram"],
             "relatedTerms": ["#brandvoice", "#lowercase", "lowercase captions"]}

    def test_topic(self):
        self.assertEqual(copywriter.topic("#lowercase / #brandvoice"), "lowercase")
        self.assertEqual(copywriter.topic('"girl dinner"'), "girl dinner")
        self.assertEqual(copywriter.topic('"okay so my email…"'), "okay so my email")

    def test_every_tone_names_the_trend_and_the_platforms(self):
        for tone in copywriter.TONES:
            with self.subTest(tone=tone):
                c = copywriter.write(self.TREND, tone)
                self.assertIn("lowercase", c.subject_line.lower())
                self.assertIn("X, TikTok and Instagram", c.body)
                self.assertNotIn("#", c.body)  # customer-facing copy: no hashtags

    def test_the_three_voices_differ(self):
        bodies = {tone: copywriter.write(self.TREND, tone) for tone in copywriter.TONES}
        self.assertEqual(len({c.subject_line for c in bodies.values()}), 3)
        self.assertIn("up 984% over their usual pace", bodies["expert"].body)  # expert leads with the numbers
        self.assertEqual(bodies["expert"].subject_line, "Lowercase, by the numbers")

    def test_unknown_tone(self):
        with self.assertRaises(ValueError):
            copywriter.write(self.TREND, "urgent")  # replaced by expert

    def test_html_is_escaped_and_has_no_button(self):
        c = copywriter.write({"name": "<script>x</script>"}, "casual")
        out = copywriter.to_html(c)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<a ", out)  # the marketer adds their own call to action in Mailchimp
        self.assertEqual(copywriter.to_plain(c), f"{c.headline}\n\n{c.body}")


class TestCampaignService(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo(payload())

    def test_new_campaign(self):
        mc = FakeMailchimp()
        out = CampaignService(self.repo, mc).draft_block({"trendId": T1, "campaignId": None, "tone": "casual"})
        self.assertEqual((out["status"], out["trendId"]), ("draft", T1))
        self.assertEqual(mc.calls, ["create", "content"])
        self.assertIn(out["campaignId"], mc.content)
        self.assertEqual(self.repo.blocks, [(out["blockId"], T1, out["campaignId"], out["suggestedCopy"])])
        self.assertIn("lowercase", out["subjectLine"].lower())
        self.assertEqual(out["previewUrl"], f"http://mailchimp.test/archive/{out['campaignId']}")
        self.assertEqual(set(out) >= {"previewText", "headline", "tone"}, True)
        self.assertNotIn("cta", out)

    def test_preview_matches_what_insert_puts_in_and_has_no_side_effects(self):
        mc = FakeMailchimp()
        svc = CampaignService(self.repo, mc)
        preview = svc.preview({"trendId": T1, "tone": "expert"})
        self.assertEqual((mc.calls, self.repo.blocks), ([], []))
        inserted = svc.draft_block({"trendId": T1, "tone": "expert"})
        for k in ("subjectLine", "previewText", "headline", "suggestedCopy", "tone"):
            self.assertEqual(preview[k], inserted[k], k)
        self.assertIn(html.escape(inserted["suggestedCopy"]), mc.content[inserted["campaignId"]])  # the HTML holds it

    def test_preview_validates_like_insert(self):
        with self.assertRaises(ApiError) as e:
            CampaignService(self.repo, FakeMailchimp()).preview({"trendId": T1, "tone": "angry"})
        self.assertEqual(e.exception.code, "bad_tone")
        with self.assertRaises(ApiError) as e:
            CampaignService(FakeRepo(None), FakeMailchimp()).preview({"trendId": T1})
        self.assertEqual(e.exception.code, "trend_not_found")

    def test_existing_campaign(self):
        mc = FakeMailchimp(existing=["42694e9e57"])
        out = CampaignService(self.repo, mc).draft_block({"trendId": T2, "campaignId": "42694e9e57"})
        self.assertEqual((mc.calls, out["campaignId"]), (["get", "content"], "42694e9e57"))

    def test_unknown_campaign_is_404_and_nothing_is_recorded(self):
        with self.assertRaises(ApiError) as e:
            CampaignService(self.repo, FakeMailchimp()).draft_block({"trendId": T1, "campaignId": "abc"})
        self.assertEqual((e.exception.status, e.exception.code), (404, "campaign_not_found"))
        self.assertEqual(self.repo.blocks, [])

    def test_unknown_trend_is_404_before_calling_mailchimp(self):
        mc = FakeMailchimp()
        with self.assertRaises(ApiError) as e:
            CampaignService(FakeRepo(None), mc).draft_block({"trendId": T1})
        self.assertEqual((e.exception.status, e.exception.code), (404, "trend_not_found"))
        self.assertEqual(mc.calls, [])

    def test_mailchimp_down_or_rejecting_is_502(self):
        for fail, code in [(MailchimpUnavailable("refused"), "mailchimp_unavailable"),
                           (MailchimpError(401, "API Key Invalid", ""), "mailchimp_error")]:
            with self.subTest(code=code), self.assertRaises(ApiError) as e:
                CampaignService(self.repo, FakeMailchimp(fail=fail)).draft_block({"trendId": T1})
            self.assertEqual((e.exception.status, e.exception.code), (502, code))
        self.assertEqual(self.repo.blocks, [])


class _Stub(BaseHTTPRequestHandler):
    """Answers like Mailchimp and records what it got."""
    seen = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        _Stub.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.path.endswith("/missing"):
            return self._reply(404, {"title": "Resource Not Found", "detail": "nope", "status": 404})
        if self.path == "/3.0/campaigns":
            return self._reply(200, {"id": "abc123def0", "status": "save"})
        return self._reply(200, {"ok": True})

    do_GET = do_POST = do_PUT = _handle


class TestMailchimpClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/3.0"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_requests_match_mailchimps_api(self):
        _Stub.seen.clear()
        c = MailchimpClient(self.url, "k-us1")
        self.assertEqual(c.create_campaign("S", "P", "T", "F", "r@x.io")["id"], "abc123def0")
        c.set_content("abc123def0", "<p>x</p>", "x")
        (m1, p1, auth, b1), (m2, p2, _, b2) = _Stub.seen
        self.assertEqual((m1, p1, m2, p2), ("POST", "/3.0/campaigns", "PUT", "/3.0/campaigns/abc123def0/content"))
        self.assertEqual(base64.b64decode(auth.split()[1]).decode().split(":")[1], "k-us1")
        self.assertEqual((b1["type"], b1["settings"]["subject_line"], b1["settings"]["reply_to"]), ("regular", "S", "r@x.io"))
        self.assertEqual(b2, {"html": "<p>x</p>", "plain_text": "x"})

    def test_error_mapping(self):
        with self.assertRaises(MailchimpError) as e:
            MailchimpClient(self.url, "k").get_campaign("missing")
        self.assertEqual((e.exception.status, e.exception.title), (404, "Resource Not Found"))
        with self.assertRaises(MailchimpUnavailable):
            MailchimpClient("http://127.0.0.1:9/3.0", "k", timeout=2).ping()  # nothing listens on port 9


if __name__ == "__main__":
    unittest.main()
