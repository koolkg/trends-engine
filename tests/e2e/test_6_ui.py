"""te-ui through its real nginx container (Phase 5): the app is served, deep links
fall back to it, and /v1 (the trend feed and the campaign POST) reaches te-api
unchanged.

Doesn't need the fixture replay, so it also runs on its own:
make -C te-deployment test-integration-te-ui"""
import json
import os
import re
import unittest
import urllib.error
import urllib.request

from e2e.harness import API_URL

UI_URL = os.environ.get("UI_URL", "http://te-ui")


def fetch(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


class UiThroughNginx(unittest.TestCase):
    def test_healthz(self):
        self.assertEqual(fetch(f"{UI_URL}/healthz")[0], 200)

    def test_app_and_its_bundle_are_served(self):
        status, headers, body = fetch(f"{UI_URL}/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertRegex(body, rb'<div id="?root"?>')  # the production build may drop attribute quotes
        scripts = re.findall(rb'src="?/?(app\.[0-9a-f]+\.js)', body)
        self.assertTrue(scripts, body[:500])
        status, headers, js = fetch(f"{UI_URL}/{scripts[0].decode()}")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn("immutable", headers.get("Cache-Control", ""))
        self.assertGreater(len(js), 10_000)

    def test_deep_links_fall_back_to_the_app(self):
        status, _, body = fetch(f"{UI_URL}/some/deep/link")
        self.assertEqual(status, 200)
        self.assertRegex(body, rb'<div id="?root"?>')  # the production build may drop attribute quotes

    def test_missing_bundle_is_a_404_not_the_app(self):
        self.assertEqual(fetch(f"{UI_URL}/app.deadbeef.js")[0], 404)

    def test_trend_feed_is_te_apis_unchanged(self):
        for _ in range(3):  # a new ranking can land between the two reads; retry then
            api_status, api_headers, api_body = fetch(f"{API_URL}/v1/trends?limit=100")
            ui_status, ui_headers, ui_body = fetch(f"{UI_URL}/v1/trends?limit=100")
            if api_headers.get("X-Trends-Version") == ui_headers.get("X-Trends-Version"):
                break
        self.assertEqual((ui_status, json.loads(ui_body)), (api_status, json.loads(api_body)))
        self.assertEqual(ui_headers.get("X-Trends-Version"), api_headers.get("X-Trends-Version"))
        items = json.loads(ui_body)["items"]
        if items:
            tid = items[0]["trendId"]
            self.assertEqual(json.loads(fetch(f"{UI_URL}/v1/trends/{tid}")[2]),
                             json.loads(fetch(f"{API_URL}/v1/trends/{tid}")[2]))

    def test_api_errors_pass_through(self):
        status, _, body = fetch(f"{UI_URL}/v1/trends?category=bogus")
        self.assertEqual((status, json.loads(body)["error"]), (400, "bad_category"))
        status, _, body = fetch(f"{UI_URL}/v1/trends/00000000-0000-0000-0000-000000000000")
        self.assertEqual((status, json.loads(body)["error"]), (404, "not_found"))

    def test_campaign_insert_goes_to_te_api(self):
        # An unknown trend: te-api's own 404 coming back through the proxy proves the route,
        # without creating a draft (the full insert is checked in test_1_happy_path).
        status, _, body = fetch(f"{UI_URL}/v1/campaigns/draft-block", "POST",
                                {"trendId": "00000000-0000-0000-0000-000000000000", "campaignId": None, "tone": "playful"})
        self.assertEqual((status, json.loads(body)["error"]), (404, "trend_not_found"))


if __name__ == "__main__":
    unittest.main()
