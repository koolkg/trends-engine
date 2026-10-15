"""One aggregation cycle, AGG-2 through AGG-6, under the single-flight lock."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from aggregator import cache
from aggregator.ranking import dedupe_windows, merge, score_signals, top
from aggregator.synthesis import synthesize

logger = logging.getLogger(__name__)


class Aggregator:
    def __init__(self, repo, redis, lock, top_n=10, lookback_hours=6.0, sparkline_hours=12, decay_per_hour=0.8,
                 min_z=1.0, spike_z=3.0, cache_ttl_seconds=3600, clock=lambda: datetime.now(timezone.utc)):
        self.repo, self.redis, self.lock = repo, redis, lock
        self.top_n, self.lookback = top_n, timedelta(hours=lookback_hours)
        self.sparkline_hours, self.decay, self.min_z, self.spike_z = sparkline_hours, decay_per_hour, min_z, spike_z
        self.ttl, self.clock = cache_ttl_seconds, clock

    def run(self, reason: str = "manual") -> Optional[List[Dict]]:
        """Returns the published ranking, or None if another instance holds the lock."""
        if not self.lock.acquire():
            logger.info("cycle (%s) skipped: another aggregation is in flight", reason)
            return None
        try:
            return self._run(reason)
        finally:
            self.lock.release()

    def _run(self, reason: str) -> List[Dict]:
        now = self.repo.data_now()
        computed_at = self.clock()
        if now is None:
            logger.info("cycle (%s): no scores from te-detector/te-cluster yet; publishing an empty ranking", reason)
            self.repo.write_ranking([], computed_at)
            cache.push(self.redis, [], computed_at, self.ttl)
            return []
        since = now - self.lookback
        terms = score_signals("term", dedupe_windows(self.repo.term_rows(since)), now, self.decay, self.min_z)
        clusters = score_signals("cluster", dedupe_windows(self.repo.cluster_rows(since)), now, self.decay, self.min_z)
        # Only fetch posts for plausible winners: a few times the final list size per signal type.
        pool = self.top_n * 3
        terms = sorted(terms, key=lambda c: -c.score)[:pool]
        clusters = sorted(clusters, key=lambda c: -c.score)[:pool]
        span_start = now - max(self.lookback, timedelta(hours=self.sparkline_hours))
        posts = {c.key: self.repo.posts_for_term(c.ref, span_start, now) for c in terms}
        posts.update({c.key: self.repo.posts_for_cluster(c.ref, span_start, now) for c in clusters})
        ranked = top(merge(terms, clusters, posts), self.top_n)
        trends = [synthesize(t, i + 1, now, self.sparkline_hours, self.spike_z) for i, t in enumerate(ranked)]
        self.repo.write_ranking(trends, computed_at)          # AGG-5: Postgres is the source of truth...
        version = cache.push(self.redis, trends, computed_at, self.ttl)  # AGG-6: ...Redis the hot copy
        logger.info("cycle (%s): %d term + %d cluster candidates -> %d trends (data now %s, cache v%s)%s", reason,
                    len(terms), len(clusters), len(trends), now.strftime("%Y-%m-%d %H:%M"), version,
                    "; top: " + ", ".join(t["name"] for t in trends[:3]) if trends else "")
        return trends
