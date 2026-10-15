"""API-1: a minimal HTTP server on the standard library (http.server), no framework.

    GET  /v1/trends?category=&limit=&cursor=
    GET  /v1/trends/{trendId}
    POST /v1/campaigns/draft-block        (campaigns.py)
    POST /v1/campaigns/draft-block/preview
    GET  /healthz

Each request thread gets its own Redis and Postgres connections (the stdlib
Redis client isn't thread-safe). Responses carry X-Trends-Source
(cache|database) and X-Trends-Version, which help debugging and let the
e2e tests observe the cache-miss fallback.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

from api.trends import ApiError, TrendService

MAX_BODY = 64 * 1024

logger = logging.getLogger(__name__)


def route(path: str):
    """-> ("list" | "detail" | "draft-block" | "draft-preview" | "health" | None, trend_id or None)"""
    parts = [p for p in path.split("/") if p]
    if parts == ["v1", "trends"]:
        return "list", None
    if parts == ["v1", "campaigns", "draft-block"]:
        return "draft-block", None
    if parts == ["v1", "campaigns", "draft-block", "preview"]:
        return "draft-preview", None
    if len(parts) == 3 and parts[:2] == ["v1", "trends"]:
        return "detail", parts[2]
    if parts == ["healthz"]:
        return "health", None
    return None, None


def make_handler(service_factory: Callable[[], TrendService], campaign_factory: Optional[Callable] = None):
    local = threading.local()

    def service() -> TrendService:
        if not hasattr(local, "svc"):
            local.svc = service_factory()
        return local.svc

    def campaigns():
        if campaign_factory is None:
            raise ApiError(404, "not_found", "campaigns are not enabled")
        if not hasattr(local, "campaigns"):
            local.campaigns = campaign_factory()
        return local.campaigns

    class Handler(BaseHTTPRequestHandler):
        server_version = "te-api/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # route http.server's access log through logging
            logger.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: dict, feed=None) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if feed is not None:
                self.send_header("X-Trends-Source", feed.source)
                self.send_header("X-Trends-Version", feed.payload.get("version", ""))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 (http.server naming)
            url = urlsplit(self.path)
            kind, arg = route(url.path)
            q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=False).items()}
            try:
                if kind in ("draft-block", "draft-preview"):
                    raise ApiError(405, "method_not_allowed", f"use POST for {url.path}")
                if kind == "list":
                    body, feed = service().list_trends(q.get("category"), q.get("limit"), q.get("cursor"))
                    return self._send(200, body, feed)
                if kind == "detail":
                    body, feed = service().get_trend(arg)
                    return self._send(200, body, feed)
                if kind == "health":
                    return self._send(200, {"status": "ok"})
                raise ApiError(404, "not_found", f"no route for {url.path}")
            except ApiError as e:
                return self._send(e.status, {"error": e.code, "message": e.message})
            except Exception:
                logger.exception("unhandled error on %s", self.path)
                return self._send(500, {"error": "internal", "message": "internal error"})

        def do_POST(self):  # noqa: N802
            kind, _ = route(urlsplit(self.path).path)
            try:
                if kind not in ("draft-block", "draft-preview"):
                    raise ApiError(405 if kind else 404, "method_not_allowed" if kind else "not_found",
                                   f"POST isn't supported on {urlsplit(self.path).path}")
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise ApiError(413, "too_large", "request body too large")
                try:
                    body = json.loads(self.rfile.read(length) or b"null")
                except ValueError:
                    raise ApiError(400, "bad_json", "body must be JSON")
                if kind == "draft-preview":
                    return self._send(200, campaigns().preview(body))
                return self._send(201, campaigns().draft_block(body))
            except ApiError as e:
                return self._send(e.status, {"error": e.code, "message": e.message})
            except Exception:
                logger.exception("unhandled error on POST %s", self.path)
                return self._send(500, {"error": "internal", "message": "internal error"})

        def _method_not_allowed(self):
            self._send(405, {"error": "method_not_allowed", "message": "only GET and POST are supported"})

        do_PUT = do_DELETE = do_PATCH = _method_not_allowed

    return Handler


def make_server(host: str, port: int, service_factory: Callable[[], TrendService],
                campaign_factory: Optional[Callable] = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service_factory, campaign_factory))
    server.daemon_threads = True
    return server
