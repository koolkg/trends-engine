"""API-2 (Redis side). Reads the feed te-aggregator pushes:

    <prefix>:current    -> "<version>"
    <prefix>:v:<version> -> {"version", "computedAt", "trends": [...]}

A miss is either key being absent: no pointer yet, or the pointer names a
version that has expired. On a miss the service reads Postgres and calls
`repopulate`, which writes the same layout te-aggregator does. The pointer is
set with NX, so if te-aggregator published a newer version meanwhile, its
pointer wins and ours is just an unused key that expires.
"""
import json
from datetime import datetime
from typing import Dict, Optional


def version_of(computed_at: datetime) -> str:
    """Same format te-aggregator uses, so a repopulated key matches its version name."""
    return computed_at.strftime("%Y%m%dT%H%M%S%fZ")


class FeedCache:
    def __init__(self, redis, prefix: str = "trends", ttl_seconds: int = 3600):
        self.redis, self.prefix, self.ttl = redis, prefix, ttl_seconds

    @property
    def current_key(self) -> str:
        return f"{self.prefix}:current"

    def version_key(self, version: str) -> str:
        return f"{self.prefix}:v:{version}"

    def read(self) -> Optional[Dict]:
        version = self.redis.get(self.current_key)
        if version is None:
            return None
        raw = self.redis.get(self.version_key(version.decode()))
        return json.loads(raw) if raw is not None else None

    def repopulate(self, payload: Dict) -> None:
        self.redis.set(self.version_key(payload["version"]), json.dumps(payload), ex=self.ttl)
        if not self.redis.set(self.current_key, payload["version"], nx=True):
            # A pointer exists but its version had expired (that's why we missed):
            # only repoint if it still names that dead version.
            current = self.redis.get(self.current_key)
            if current is not None and self.redis.get(self.version_key(current.decode())) is None:
                self.redis.set(self.current_key, payload["version"])
