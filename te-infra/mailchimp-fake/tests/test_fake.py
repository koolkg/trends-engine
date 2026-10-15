"""The fake on an ephemeral port: auth, campaign + content round trip, problem errors."""
import base64
import json
import threading
import unittest
import urllib.error
import urllib.request

from mailchimp_fake import make_server

KEY = "test-key-us1"


def call(port, method, path, body=None, key=KEY):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if key:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"anystring:{key}".encode()).decode())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), json.loads(e.read())


class FakeMailchimp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, KEY)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_ping_and_auth(self):
        self.assertEqual(call(self.port, "GET", "/3.0/ping")[0:3:2], (200, {"health_status": "Everything's Chimpy!"}))
        status, ctype, body = call(self.port, "GET", "/3.0/ping", key="wrong")
        self.assertEqual((status, body["title"]), (401, "API Key Invalid"))
        self.assertIn("problem+json", ctype)
        self.assertEqual(call(self.port, "GET", "/3.0/ping", key=None)[0], 401)

    def test_create_campaign_then_content(self):
        status, _, c = call(self.port, "POST", "/3.0/campaigns",
                            {"type": "regular", "settings": {"subject_line": "Hi", "title": "Trend: x"}})
        self.assertEqual((status, c["status"], c["settings"]["subject_line"]), (200, "save", "Hi"))
        self.assertRegex(c["id"], r"^[0-9a-f]{10}$")
        status, _, content = call(self.port, "PUT", f"/3.0/campaigns/{c['id']}/content",
                                  {"html": "<h1>Hello</h1><p>world</p>"})
        self.assertEqual((status, content["plain_text"]), (200, "Hello world"))
        self.assertEqual(call(self.port, "GET", f"/3.0/campaigns/{c['id']}/content")[2]["html"],
                         "<h1>Hello</h1><p>world</p>")
        listed = call(self.port, "GET", "/3.0/campaigns")[2]
        self.assertIn(c["id"], [x["id"] for x in listed["campaigns"]])

    def test_archive_page_shows_the_draft(self):
        c = call(self.port, "POST", "/3.0/campaigns",
                 {"type": "regular", "settings": {"subject_line": "Lowercase <is> here", "preview_text": "pt"}})[2]
        self.assertEqual(c["long_archive_url"], f"http://localhost:8090/archive/{c['id']}")
        call(self.port, "PUT", f"/3.0/campaigns/{c['id']}/content",
             {"html": "<html><body><h1>Big news</h1><p>copy</p></body></html>"})
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/archive/{c['id']}", timeout=5) as r:  # no auth
            page = r.read().decode()
            self.assertIn("text/html", r.headers.get("Content-Type"))
        self.assertIn("<h1>Big news</h1><p>copy</p>", page)
        self.assertIn("Subject: Lowercase &lt;is&gt; here", page)  # escaped
        self.assertEqual(page.count("<body"), 1)  # the email's own <body> is unwrapped

    def test_errors(self):
        self.assertEqual(call(self.port, "GET", "/3.0/campaigns/0000000000")[0], 404)
        self.assertEqual(call(self.port, "PUT", "/3.0/campaigns/0000000000/content", {"html": "x"})[0], 404)
        self.assertEqual(call(self.port, "POST", "/3.0/campaigns", {"type": "nope"})[0], 400)
        c = call(self.port, "POST", "/3.0/campaigns", {"type": "regular"})[2]
        self.assertEqual(call(self.port, "PUT", f"/3.0/campaigns/{c['id']}/content", {})[0], 400)
        self.assertEqual(call(self.port, "DELETE", f"/3.0/campaigns/{c['id']}")[0], 405)
        self.assertEqual(call(self.port, "GET", "/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
