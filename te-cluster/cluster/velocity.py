"""CLU-5: per-cluster growth in event-time windows. It's the same windowing
as te-detector's detector.py (keyed on postedAt; a window closes when a later
post arrives or on an idle flush; warmup; min-count filter), but it counts
*new members per cluster* instead of hashtags.

A freshly seeded singleton has a window count of 1 and no real baseline, so
it can't clear min_count or produce a meaningful z-score. That's how
one-off clusters stay out of te-aggregator without an explicit "noise" label
(design doc, "Cluster lifecycle").
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from cluster.scoring import Baseline, velocity_label

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ClusterScore:
    cluster_id: str
    window_start: datetime
    window_end: datetime
    frequency: int              # new members this window
    baseline_frequency: float   # baseline mean before this window
    z_score: float
    velocity_label: str


class VelocityTracker:
    def __init__(self, window_minutes=60, alpha=0.3, z_threshold=3.0, min_count=4, warmup_windows=2):
        self.window = timedelta(minutes=window_minutes)
        self.alpha, self.z_threshold = alpha, z_threshold
        self.min_count, self.warmup_windows = min_count, warmup_windows
        self.open_counts: Dict[datetime, Counter] = defaultdict(Counter)
        self.current: Optional[datetime] = None
        self.baselines: Dict[str, Baseline] = {}
        self.windows_closed = 0
        self.late_posts = 0

    def window_start(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return EPOCH + ((ts - EPOCH) // self.window) * self.window

    def add(self, posted_at: datetime, cluster_id: str) -> List[ClusterScore]:
        start = self.window_start(posted_at)
        if self.current is None:
            self.current = start
        if start < self.current:
            self.late_posts += 1
            return []
        self.open_counts[start][cluster_id] += 1
        scores: List[ClusterScore] = []
        while self.current < start:
            scores += self._close(self.current)
            self.current += self.window
        return scores

    def flush(self) -> List[ClusterScore]:
        if self.current is None:
            return []
        scores = self._close(self.current)
        self.current += self.window
        return scores

    def forget(self, cluster_ids) -> None:
        """Evicted clusters stop being tracked."""
        for cid in cluster_ids:
            self.baselines.pop(cid, None)

    def _close(self, start: datetime) -> List[ClusterScore]:
        counts = self.open_counts.pop(start, Counter())
        warming_up = self.windows_closed < self.warmup_windows
        out: List[ClusterScore] = []
        for cid in set(counts) | set(self.baselines):
            b = self.baselines.get(cid) or self.baselines.setdefault(cid, Baseline(self.alpha))
            n = counts.get(cid, 0)
            z, before = b.z(n), b.mean
            b.update(n)
            if not warming_up and n >= self.min_count:
                out.append(ClusterScore(cid, start, start + self.window, n, round(before, 2), round(z, 2),
                                        velocity_label(z, self.z_threshold)))
            if b.negligible:
                del self.baselines[cid]
        self.windows_closed += 1
        out.sort(key=lambda s: (-s.z_score, s.cluster_id))
        return out
