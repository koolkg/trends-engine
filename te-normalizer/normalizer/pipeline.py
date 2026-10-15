"""Per-message processing: parse -> clean (NORM-2) -> relevance + category (NORM-4, relevance.py) ->
store + dedup (NORM-5/NORM-3) -> publish content.normalized (NORM-6).
No broker or database code lives here. Store and publisher are passed in,
so this is unit-testable with fakes."""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from normalizer.cleanup import clean_text
from normalizer.dedup import dedup_hash
from normalizer.relevance import KeywordRelevance

logger = logging.getLogger(__name__)

NORMALIZED_KEY = "content.normalized"
PUBLISHED, IRRELEVANT, DUPLICATE, INVALID = "published", "irrelevant", "duplicate", "invalid"


class InvalidMessage(ValueError):
    """The message can never be processed (bad JSON, missing fields). Dropped, not retried."""


@dataclass(frozen=True)
class RawItem:
    source_id: str
    source_type: str
    content_id: str
    raw_text: str
    author: Optional[str]
    posted_at: Optional[datetime]
    posted_at_raw: Optional[str]
    likes: int
    shares: int
    comments: int


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse(body: bytes) -> RawItem:
    """Validates one content.raw.ingested message (its event schema)."""
    try:
        msg = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidMessage(f"not JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise InvalidMessage("not a JSON object")
    missing = [k for k in ("sourceId", "contentId", "rawText") if not msg.get(k)]
    if missing:
        raise InvalidMessage(f"missing required field(s): {', '.join(missing)}")
    posted_raw = msg.get("postedAt")
    posted = None
    if posted_raw:
        try:
            posted = datetime.fromisoformat(str(posted_raw).replace("Z", "+00:00"))
        except ValueError:
            logger.warning("unparseable postedAt %r on %s; storing NULL", posted_raw, msg["contentId"])
            posted_raw = None
    eng = msg.get("engagement") if isinstance(msg.get("engagement"), dict) else {}
    return RawItem(
        source_id=str(msg["sourceId"]),
        source_type=str(msg.get("sourceType") or "unknown"),
        content_id=str(msg["contentId"]),
        raw_text=str(msg["rawText"]),
        author=msg.get("author"),
        posted_at=posted,
        posted_at_raw=posted_raw,
        likes=_int(eng.get("likes")),
        shares=_int(eng.get("shares")),
        comments=_int(eng.get("comments")),
    )


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def process(body: bytes, store, publisher, threshold: float = 0.5, relevance=None) -> str:
    """Returns the outcome: published / irrelevant / duplicate. Raises
    InvalidMessage for a poison message; anything else (DB/broker down)
    propagates so the caller can requeue and retry. `relevance` is the
    classifier from relevance.build(); without one, the keyword table decides."""
    item = parse(body)
    cleaned = clean_text(item.raw_text)
    result = (relevance or KeywordRelevance(threshold)).decide(cleaned)
    relevant = result.relevant
    saved = store.save(item, cleaned, dedup_hash(cleaned), relevant, result.relevance_score, result.category)
    if not relevant:
        return IRRELEVANT
    if saved.normalized_id is None:
        return DUPLICATE
    publisher.publish(NORMALIZED_KEY, {
        "normalizedId": saved.normalized_id,
        "rawContentId": saved.raw_id,
        "sourceId": item.source_id,
        "contentId": item.content_id,
        "cleanedText": cleaned,
        "category": result.category,
        "relevanceScore": result.relevance_score,
        "classifiedBy": result.method,
        "author": item.author,
        "postedAt": item.posted_at_raw,
        "processedAt": _iso(saved.processed_at),
        "engagement": {"likes": item.likes, "shares": item.shares, "comments": item.comments},
    })
    return PUBLISHED
