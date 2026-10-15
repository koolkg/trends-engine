"""Per-micro-batch processing: parse -> one embedding call for the whole batch
-> join-or-seed each post (in order) -> per-cluster growth windows -> cold
eviction -> persist (one transaction) -> publish growth scores.

No broker code here; embedder, store and publisher are injected, so the unit
tests run it with a deterministic stand-in embedder and fakes.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from cluster.centroids import Assignment, CentroidIndex
from cluster.velocity import ClusterScore, VelocityTracker

logger = logging.getLogger(__name__)

SCORE_KEY = "cluster.score.upserted"


class InvalidMessage(ValueError):
    """Can never be processed: dropped (acked), not retried."""


@dataclass(frozen=True)
class Post:
    normalized_id: int
    text: str
    posted_at: datetime


def parse(body: bytes) -> Post:
    try:
        msg = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidMessage(f"not JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise InvalidMessage("not a JSON object")
    nid, text, posted = msg.get("normalizedId"), msg.get("cleanedText"), msg.get("postedAt")
    if not isinstance(nid, int) or isinstance(nid, bool) or not isinstance(text, str) or not text.strip() or not posted:
        raise InvalidMessage("needs integer normalizedId, non-empty cleanedText and postedAt")
    try:
        when = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidMessage(f"unparseable postedAt {posted!r}") from exc
    return Post(nid, text, when)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def score_message(s: ClusterScore, member_count: Optional[int]) -> dict:
    """cluster.score.upserted: same shape as te-detector's trend.score.upserted,
    keyed by clusterId instead of term (design doc: the two are merged as equals)."""
    return {"clusterId": s.cluster_id, "windowStart": _iso(s.window_start), "windowEnd": _iso(s.window_end),
            "frequency": s.frequency, "baselineFrequency": s.baseline_frequency, "zScore": s.z_score,
            "velocityLabel": s.velocity_label, "memberCount": member_count}


@dataclass
class BatchResult:
    assignments: Dict[int, Assignment]   # normalizedId -> where it went
    invalid: int
    scores: List[ClusterScore]
    evicted: List[str]


class Handler:
    def __init__(self, embedder, index: CentroidIndex, velocity: VelocityTracker, store, publisher=None):
        self.embedder, self.index, self.velocity, self.store = embedder, index, velocity, store
        self.publisher = publisher  # set per connection by the consumer
        self.watermark: Optional[datetime] = None

    def load(self) -> None:
        """(Re)loads persisted centroids, at startup or after a failed batch, so
        memory never runs ahead of what's actually in Postgres."""
        self.index.load(self.store.load_centroids())
        logger.info("centroid index loaded: %d active clusters", len(self.index))

    def process_batch(self, bodies: List[bytes]) -> BatchResult:
        posts, invalid = [], 0
        for body in bodies:
            try:
                posts.append(parse(body))
            except InvalidMessage as exc:
                logger.warning("dropping invalid message: %s", exc)
                invalid += 1
        done = self.store.already_assigned([p.normalized_id for p in posts])
        if done:
            logger.info("skipping %d already-clustered posts (redelivery)", len(done))
            posts = [p for p in posts if p.normalized_id not in done]
        if not posts:
            return BatchResult({}, invalid, [], [])
        vectors = self.embedder.encode([p.text for p in posts])  # one forward pass per micro-batch
        assignments: Dict[int, Assignment] = {}
        touched, scores = {}, []
        try:
            for post, v in zip(posts, vectors):
                a = self.index.assign(v, post.posted_at)
                assignments[post.normalized_id] = a
                touched[a.cluster_id] = None
                scores += self.velocity.add(post.posted_at, a.cluster_id)
                self.watermark = max(self.watermark or post.posted_at, post.posted_at)
            evicted = self.index.evict_stale(self.watermark)
            self.velocity.forget(evicted)
            self.store.save_batch(
                [(nid, a.cluster_id, a.similarity) for nid, a in assignments.items()],
                [self.index.clusters[cid] for cid in touched if cid in self.index.clusters],
                evicted, scores)
        except Exception:
            self.load()  # resync memory with the database before the batch is redelivered
            raise
        self._publish(scores)
        seeded = sum(a.seeded for a in assignments.values())
        logger.info("batch of %d: %d joined, %d seeded, %d active clusters%s", len(posts), len(posts) - seeded,
                    seeded, len(self.index), f", {len(evicted)} evicted" if evicted else "")
        return BatchResult(assignments, invalid, scores, evicted)

    def on_idle(self) -> List[ClusterScore]:
        scores = self.velocity.flush()
        if scores:
            self.store.save_batch([], [], [], scores)
            self._publish(scores)
        return scores

    def _publish(self, scores: List[ClusterScore]) -> None:
        for s in scores:
            c = self.index.clusters.get(s.cluster_id)
            self.publisher.publish(SCORE_KEY, score_message(s, c.member_count if c else None))
        spikes = sum(s.velocity_label == "spike" for s in scores)
        if scores:
            logger.info("window %s closed: %d clusters scored, %d spiking", _iso(scores[0].window_start),
                        len(scores), spikes)
