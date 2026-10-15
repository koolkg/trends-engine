"""API-2/3/4: the feed and detail logic, independent of HTTP.

Reads the cached ranking (Redis); on a miss, falls back to Postgres and
repopulates the cache, and never recomputes the ranking. Shapes responses to
the design's API schemas. Extra fields are additive.
"""
import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CATEGORIES = ("email", "social", "ecommerce", "content")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

LIST_FIELDS = ("trendId", "name", "category", "velocityScore", "deltaPct", "firstSeenAt", "sources", "sparkline")
LIST_EXTRAS = ("rank", "velocityLabel", "summary")
DETAIL_FIELDS = ("trendId", "clusterId", "relatedTerms", "samplePosts", "growth")
DETAIL_EXTRAS = ("name", "category", "summary", "rank", "velocityScore", "velocityLabel", "deltaPct", "firstSeenAt",
                 "sources", "sparkline", "highlights", "signals")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def encode_cursor(offset: int) -> str:
    """Opaque cursor, as in the doc's example ("eyJvZmZzZXQiOjIwfQ" == {"offset":20})."""
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = json.loads(base64.urlsafe_b64decode(padded.encode()))["offset"]
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise ApiError(400, "bad_cursor", "cursor is not one this API issued")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ApiError(400, "bad_cursor", "cursor is not one this API issued")
    return offset


def shape_list_item(t: Dict) -> Dict:
    return {k: t.get(k) for k in LIST_FIELDS + LIST_EXTRAS}


def shape_detail(t: Dict) -> Dict:
    out = {k: t.get(k) for k in DETAIL_FIELDS + DETAIL_EXTRAS}
    out["samplePosts"] = [{k: p.get(k) for k in ("text", "source", "postedAt", "author")}
                          for p in (t.get("samplePosts") or [])]
    return out


@dataclass
class Feed:
    payload: Dict
    source: str  # "cache" | "database"


class TrendService:
    def __init__(self, cache, repo, default_limit: int = 20, max_limit: int = 100):
        self.cache, self.repo = cache, repo
        self.default_limit, self.max_limit = default_limit, max_limit

    def _feed(self) -> Optional[Feed]:
        try:
            payload = self.cache.read()
        except (OSError, ConnectionError) as exc:  # Redis down: degrade to Postgres, don't fail the request
            logger.warning("redis unavailable (%s); serving from postgres", exc)
            payload = None
            redis_ok = False
        else:
            redis_ok = True
        if payload is not None:
            return Feed(payload, "cache")
        payload = self.repo.ranked_feed()
        if payload is None:
            return None
        if redis_ok:
            try:
                self.cache.repopulate(payload)
                logger.info("cache miss: served version %s from postgres and repopulated redis", payload["version"])
            except (OSError, ConnectionError) as exc:
                logger.warning("could not repopulate redis: %s", exc)
        return Feed(payload, "database")

    def list_trends(self, category: Optional[str] = None, limit: Optional[str] = None,
                    cursor: Optional[str] = None) -> Tuple[Dict, Optional[Feed]]:
        if category is not None and category not in CATEGORIES:
            raise ApiError(400, "bad_category", f"category must be one of {', '.join(CATEGORIES)}")
        try:
            n = self.default_limit if limit is None else int(limit)
        except ValueError:
            raise ApiError(400, "bad_limit", "limit must be an integer")
        if not 1 <= n <= self.max_limit:
            raise ApiError(400, "bad_limit", f"limit must be between 1 and {self.max_limit}")
        offset = decode_cursor(cursor) if cursor else 0
        feed = self._feed()
        trends: List[Dict] = feed.payload["trends"] if feed else []
        if category:
            trends = [t for t in trends if t.get("category") == category]
        page = trends[offset:offset + n]
        next_cursor = encode_cursor(offset + n) if offset + n < len(trends) else None
        return {"items": [shape_list_item(t) for t in page], "nextCursor": next_cursor}, feed

    def get_trend(self, trend_id: str) -> Tuple[Dict, Feed]:
        trend_id = trend_id.lower()  # UUIDs are case-insensitive
        if not UUID_RE.match(trend_id):
            raise ApiError(404, "not_found", "trend not found")
        feed = self._feed()
        for t in (feed.payload["trends"] if feed else []):
            if t.get("trendId") == trend_id:
                return shape_detail(t), feed
        raise ApiError(404, "not_found", "trend not found or aged out of the ranking window")
