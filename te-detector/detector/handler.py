"""Per-message glue between the broker and the Detector: parse a
content.normalized message, feed it in, then persist and publish whatever
windows that closed. No broker or database code lives here. Store and
publisher are injected, so it's unit-tested with fakes."""
import json
import logging
from datetime import datetime
from typing import List, Optional

from detector.detector import Detector, Score

logger = logging.getLogger(__name__)

SCORE_KEY = "trend.score.upserted"


class InvalidMessage(ValueError):
    """Can never be processed (bad JSON, missing/unparseable fields): dropped, not retried."""


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def score_message(score: Score) -> dict:
    """trend.score.upserted, per the design's event schema. One message per
    emitted term per window. velocityLabel "spike" is what wakes te-aggregator
    immediately (no separate spike event; see README)."""
    return {
        "term": score.term,
        "windowStart": _iso(score.window_start),
        "windowEnd": _iso(score.window_end),
        "frequency": score.frequency,
        "baselineFrequency": score.baseline_frequency,
        "zScore": score.z_score,
        "velocityLabel": score.velocity_label,
    }


def parse(body: bytes):
    try:
        msg = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidMessage(f"not JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise InvalidMessage("not a JSON object")
    text, posted = msg.get("cleanedText"), msg.get("postedAt")
    if not isinstance(text, str) or not posted:
        raise InvalidMessage("missing cleanedText or postedAt")
    try:
        posted_at = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidMessage(f"unparseable postedAt {posted!r}") from exc
    return posted_at, text


class Handler:
    def __init__(self, detector: Detector, store, publisher=None):
        self.detector = detector
        self.store = store
        self.publisher = publisher  # set per connection by the consumer
        # Scores from windows already closed in memory but not yet stored and
        # published (e.g. Postgres was down). Retried on the next message, so a
        # closed window's scores aren't lost along with the failed attempt.
        self.pending: List[Score] = []

    def on_message(self, body: bytes) -> List[Score]:
        posted_at, text = parse(body)
        return self._emit(self.detector.add(posted_at, text))

    def on_idle(self) -> List[Score]:
        return self._emit(self.detector.flush())

    def _emit(self, scores: List[Score]) -> List[Score]:
        self.pending.extend(scores)
        if not self.pending:
            return []
        scores, self.pending = self.pending, []
        try:
            self.store.save(scores)  # persist first, then announce
            for s in scores:
                self.publisher.publish(SCORE_KEY, score_message(s))
        except Exception:
            self.pending = scores + self.pending
            raise
        spikes = [s.term for s in scores if s.velocity_label == "spike"]
        logger.info("window %s closed: %d terms scored%s", _iso(scores[0].window_start), len(scores),
                    f", spikes: {', '.join(spikes)}" if spikes else "")
        return scores
