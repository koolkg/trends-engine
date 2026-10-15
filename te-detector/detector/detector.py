"""DET-3: event-time windowed counting, plus the glue that turns a closed
window into scores (DET-4/5).

Windows are keyed on each post's `postedAt` (event time), not on when the
message arrives. te-connector replays a day of posts in a couple of hours, so
arrival time would squash the real timeline. A window is closed (scored)
once a post from a *later* window shows up, i.e. the stream's watermark moves
past it. Posts arrive in roughly chronological order, so that's enough. A
post for a window that's already closed is "late": it's counted in
`late_posts` and otherwise ignored.

Closing a window scores every term that's either in it or still has a
baseline. A term absent this window counts as 0, so its baseline decays.
Only terms with count >= min_count become Score records (which are written
and published). The rest just update their baseline silently, which keeps
output volume tiny. The first `warmup_windows` windows only build baselines:
at cold start every term is new, so everything would otherwise look like
a spike.

Pure logic: no I/O. The consumer feeds it and persists/publishes what it returns.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, List, Optional

from detector.hashtags import extract_hashtags
from detector.scoring import Baseline, velocity_label

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Score:
    term: str
    window_start: datetime
    window_end: datetime
    frequency: int
    baseline_frequency: float  # baseline mean *before* this window was folded in
    z_score: float
    velocity_label: str


class Detector:
    def __init__(self, window_minutes: int = 60, alpha: float = 0.3, z_threshold: float = 3.0,
                 min_count: int = 4, warmup_windows: int = 2, ignore_tags: FrozenSet[str] = frozenset()):
        if window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        self.window = timedelta(minutes=window_minutes)
        self.alpha = alpha
        self.z_threshold = z_threshold
        self.min_count = min_count
        self.warmup_windows = warmup_windows
        self.ignore_tags = ignore_tags
        self.open_counts: Dict[datetime, Counter] = defaultdict(Counter)
        self.current: Optional[datetime] = None  # start of the oldest still-open window
        self.baselines: Dict[str, Baseline] = {}
        self.windows_closed = 0
        self.late_posts = 0

    def window_start(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return EPOCH + ((ts - EPOCH) // self.window) * self.window

    def add(self, posted_at: datetime, text: str) -> List[Score]:
        """Counts one post's hashtags. Returns scores for any windows this
        post's timestamp caused to close (usually none)."""
        start = self.window_start(posted_at)
        if self.current is None:
            self.current = start
        if start < self.current:
            self.late_posts += 1
            return []
        for tag in extract_hashtags(text, self.ignore_tags):
            self.open_counts[start][tag] += 1
        return self._close_before(start)

    def flush(self) -> List[Score]:
        """Closes the oldest open window regardless of watermark, e.g. after the
        stream has gone quiet, so the last window of a replay still gets scored."""
        if self.current is None:
            return []
        scores = self._close(self.current)
        self.current += self.window
        return scores

    def _close_before(self, start: datetime) -> List[Score]:
        scores: List[Score] = []
        while self.current < start:  # also walks empty gap windows, so baselines decay through them
            scores.extend(self._close(self.current))
            self.current += self.window
        return scores

    def _close(self, start: datetime) -> List[Score]:
        counts = self.open_counts.pop(start, Counter())
        warming_up = self.windows_closed < self.warmup_windows
        scores: List[Score] = []
        for term in set(counts) | set(self.baselines):
            baseline = self.baselines.get(term)
            if baseline is None:
                baseline = self.baselines[term] = Baseline(self.alpha)
            count = counts.get(term, 0)
            z = baseline.z(count)
            mean_before = baseline.mean
            baseline.update(count)
            if not warming_up and count >= self.min_count:
                scores.append(Score(term, start, start + self.window, count, round(mean_before, 2),
                                    round(z, 2), velocity_label(z, self.z_threshold)))
            if baseline.negligible:
                del self.baselines[term]  # keep state bounded: long-dead terms are forgotten
        self.windows_closed += 1
        scores.sort(key=lambda s: (-s.z_score, s.term))
        return scores
