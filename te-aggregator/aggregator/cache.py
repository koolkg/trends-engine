"""AGG-6: eager push to Redis. Each cycle writes a new versioned key, then
atomically repoints `trends:current` at it. The previous version just expires
on its TTL, so a reader mid-request never sees a half-written feed.

    trends:v:<version>  -> {"version", "computedAt", "trends": [...]}   (TTL)
    trends:current      -> "<version>"
"""
import json
from datetime import datetime
from typing import Dict, List

CURRENT_KEY = "trends:current"
VERSION_KEY = "trends:v:{}"


def push(redis, trends: List[Dict], computed_at: datetime, ttl_seconds: int) -> str:
    version = computed_at.strftime("%Y%m%dT%H%M%S%fZ")
    payload = {"version": version, "computedAt": computed_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "trends": trends}
    redis.set(VERSION_KEY.format(version), json.dumps(payload), ex=ttl_seconds)
    redis.set(CURRENT_KEY, version)
    return version
