"""A stand-in for Mailchimp's Marketing API (v3), for the PoC: a stubbed
adapter returning a plausible response. te-api talks
to it exactly as it would to https://<dc>.api.mailchimp.com/3.0, so pointing
te-api at the real API is a URL and key change.

Only what te-api uses, with the real API's request/response shapes:

    GET  /3.0/ping                         {"health_status": "Everything's Chimpy!"}
    POST /3.0/campaigns                    create a draft campaign      -> 200 campaign
    GET  /3.0/campaigns                    list campaigns               -> 200 {"campaigns", "total_items"}
    GET  /3.0/campaigns/{id}               one campaign                 -> 200 | 404
    PUT  /3.0/campaigns/{id}/content       set its html / plain_text    -> 200 content
    GET  /3.0/campaigns/{id}/content       read it back                 -> 200 | 404

Each campaign also has a web view, like the real `long_archive_url`:
GET /archive/{id} (no auth) shows the draft email as stored, with its subject
line and preview text on top. That's where te-ui's "Open the draft" goes.

Auth is HTTP Basic with any username and the API key as the password, like
the real API (401 otherwise). Errors use Mailchimp's problem-detail JSON
({"type", "title", "status", "detail", "instance"}). State is in memory: a
restart forgets every campaign, which is fine for a fake.

Stdlib only. MAILCHIMP_FAKE_PORT (8090), MAILCHIMP_FAKE_API_KEY (fake-key-us1),
MAILCHIMP_FAKE_PUBLIC_URL (http://localhost:8090: how your browser reaches it,
used in the archive links).
"""
import base64
import html
import json
import logging
import os
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("mailchimp-fake")

CAMPAIGN_TYPES = {"regular", "plaintext", "absplit", "rss", "variate"}
DOCS = "https://mailchimp.com/developer/marketing/docs/errors/"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class ApiProblem(Exception):
    def __init__(self, status: int, title: str, detail: str):
        super().__init__(detail)
        self.status, self.title, self.detail = status, title, detail


class Store:
    """Campaigns and their content, in memory, thread-safe."""

    def __init__(self, public_url: str = "http://localhost:8090"):
        self.public_url = public_url.rstrip("/")
        self._lock = threading.Lock()
        self.campaigns: Dict[str, Dict] = {}
        self.content: Dict[str, Dict] = {}
        self._web_id = 100000

    def create(self, body: Dict) -> Dict:
        ctype = body.get("type")
        if ctype not in CAMPAIGN_TYPES:
            raise ApiProblem(400, "Invalid Resource", f"The resource submitted could not be validated: "
                                                      f"type must be one of {sorted(CAMPAIGN_TYPES)}")
        settings = body.get("settings") or {}
        if not isinstance(settings, dict):
            raise ApiProblem(400, "Invalid Resource", "settings must be an object")
        with self._lock:
            self._web_id += 1
            cid = secrets.token_hex(5)  # Mailchimp campaign ids are 10 hex characters
            campaign = {
                "id": cid, "web_id": self._web_id, "type": ctype, "create_time": _now(),
                "status": "save",  # Mailchimp's name for a draft
                "emails_sent": 0, "content_type": "html",
                "recipients": body.get("recipients") or {"list_id": ""},
                "settings": {k: settings.get(k, "") for k in
                             ("subject_line", "preview_text", "title", "from_name", "reply_to")},
                "archive_url": f"{self.public_url}/archive/{cid}",
                "long_archive_url": f"{self.public_url}/archive/{cid}",
            }
            self.campaigns[cid] = campaign
            return campaign

    def get(self, cid: str) -> Dict:
        with self._lock:
            if cid not in self.campaigns:
                raise ApiProblem(404, "Resource Not Found", "The requested resource could not be found.")
            return self.campaigns[cid]

    def set_content(self, cid: str, body: Dict) -> Dict:
        self.get(cid)
        if not any(k in body for k in ("html", "plain_text", "template", "url")):
            raise ApiProblem(400, "Invalid Resource", "Provide html, plain_text, template or url.")
        html = body.get("html") or ""
        plain = body.get("plain_text") or re.sub(r"<[^>]+>", " ", html)
        content = {"html": html, "plain_text": " ".join(plain.split())}
        with self._lock:
            self.content[cid] = content
        return content

    def get_content(self, cid: str) -> Dict:
        self.get(cid)
        with self._lock:
            return self.content.get(cid, {"html": "", "plain_text": ""})

    def list(self) -> Dict:
        with self._lock:
            items = sorted(self.campaigns.values(), key=lambda c: c["web_id"])
        return {"campaigns": items, "total_items": len(items)}


def archive_page(campaign: Dict, content: Dict) -> str:
    """The draft as a reader would see it, under a strip with its subject and preview text."""
    s, e = campaign["settings"], html.escape
    body = content.get("html") or "<p><em>This draft has no content yet.</em></p>"
    body = re.sub(r"(?is)^.*?<body[^>]*>|</body>.*$", "", body)  # the email's own <body> contents
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(s['subject_line'] or s['title'])}</title>"
            "<style>body{margin:0;font-family:-apple-system,Segoe UI,sans-serif;background:#f6f4ee;color:#231e15}"
            ".meta{background:#231e15;color:#fff;padding:12px 20px;font-size:13px;line-height:1.6}"
            ".meta b{color:#ffe01b}.mail{max-width:600px;margin:24px auto;background:#fff;padding:28px;"
            "border:1px solid #eae4d6;border-radius:8px}</style></head><body>"
            f"<div class='meta'><b>Draft campaign {e(campaign['id'])}</b> (fake Mailchimp) · "
            f"{e(s['title'])}<br>Subject: {e(s['subject_line'])}<br>Preview text: {e(s['preview_text'])}</div>"
            f"<div class='mail'>{body}</div></body></html>")


def authorized(header: Optional[str], api_key: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return password == api_key


def make_handler(store: Store, api_key: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mailchimp-fake/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            logger.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body, content_type="application/json; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _problem(self, e: ApiProblem):
            self._send(e.status, {"type": DOCS, "title": e.title, "status": e.status, "detail": e.detail,
                                  "instance": str(uuid.uuid4())}, "application/problem+json; charset=utf-8")

        def _body(self) -> Dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise ApiProblem(400, "JSON Parse Exception", "We encountered an unspecified JSON parsing error.")
            if not isinstance(body, dict):
                raise ApiProblem(400, "JSON Parse Exception", "The request body must be a JSON object.")
            return body

        def _handle(self, method: str):
            parts = [p for p in urlsplit(self.path).path.split("/") if p]
            try:
                if parts == ["healthz"]:  # for the container healthcheck; not part of Mailchimp's API
                    return self._send(200, {"status": "ok"})
                if len(parts) == 2 and parts[0] == "archive" and method == "GET":  # public, like the real archive
                    campaign = store.get(parts[1])
                    return self._send(200, archive_page(campaign, store.get_content(parts[1])),
                                      "text/html; charset=utf-8")
                if parts[:1] != ["3.0"]:
                    raise ApiProblem(404, "Resource Not Found", "The requested resource could not be found.")
                if not authorized(self.headers.get("Authorization"), api_key):
                    raise ApiProblem(401, "API Key Invalid", "Your API key may be invalid, or you've attempted "
                                                             "to access the wrong datacenter.")
                route = parts[1:]
                if route == ["ping"] and method == "GET":
                    return self._send(200, {"health_status": "Everything's Chimpy!"})
                if route == ["campaigns"] and method == "POST":
                    return self._send(200, store.create(self._body()))
                if route == ["campaigns"] and method == "GET":
                    return self._send(200, store.list())
                if len(route) == 2 and route[0] == "campaigns" and method == "GET":
                    return self._send(200, store.get(route[1]))
                if len(route) == 3 and route[0] == "campaigns" and route[2] == "content":
                    if method == "PUT":
                        return self._send(200, store.set_content(route[1], self._body()))
                    if method == "GET":
                        return self._send(200, store.get_content(route[1]))
                raise ApiProblem(405 if route[:1] == ["campaigns"] else 404,
                                 "Method Not Allowed" if route[:1] == ["campaigns"] else "Resource Not Found",
                                 f"{method} {self.path} is not supported by this fake")
            except ApiProblem as e:
                return self._problem(e)
            except Exception:
                logger.exception("unhandled error on %s %s", method, self.path)
                return self._problem(ApiProblem(500, "Internal Server Error", "unexpected error in the fake"))

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

        def do_PUT(self):  # noqa: N802
            self._handle("PUT")

        def do_PATCH(self):  # noqa: N802
            self._handle("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._handle("DELETE")

    return Handler


def make_server(host: str, port: int, api_key: str, store: Optional[Store] = None,
                public_url: str = "http://localhost:8090") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store or Store(public_url), api_key))
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("MAILCHIMP_FAKE_PORT", "8090"))
    server = make_server("0.0.0.0", port, os.environ.get("MAILCHIMP_FAKE_API_KEY", "fake-key-us1"),
                         public_url=os.environ.get("MAILCHIMP_FAKE_PUBLIC_URL", f"http://localhost:{port}"))
    logger.info("fake Mailchimp Marketing API on :%d (/3.0)", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
