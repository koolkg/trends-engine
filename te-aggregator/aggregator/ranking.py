"""AGG-2 + AGG-3: turn recent score rows into one ranked list of trends.

1. Each signal (a hashtag from te-detector, or a cluster from te-cluster) gets
   a velocity score: its best z-score in the lookback window, discounted by
   how many hours ago that window ended (decay^hours). A signal never above
   `min_z` (never "rising") isn't a candidate. Big-but-steady never ranks.
2. A hashtag and a cluster are the *same* trend when the tag appears in at
   least `merge_share` of the cluster's posts. They merge into one trend
   carrying both signals and the higher score. This is where the design doc's
   "two independent signals come back together".
3. Sort by score; the top N go on to synthesis.

"Now" is the newest window end in the data, not the wall clock, so
a replayed day ranks the same as a live one.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

HASHTAG = re.compile(r"(?<![\w#])#(\w{2,})")


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    frequency: int
    baseline: float
    z: float


@dataclass
class Candidate:
    kind: str            # "term" | "cluster"
    ref: str             # "#lowercase" or a cluster UUID
    score: float         # decayed best z
    best: Window
    hours_ago: float     # age of the best window
    windows: List[Window]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.ref}"


@dataclass
class Post:
    normalized_id: int
    text: str
    category: Optional[str]
    posted_at: datetime
    author: Optional[str]
    source: str          # platform: reddit / x / instagram / tiktok
    engagement: int


@dataclass
class Trend:
    score: float
    terms: List[Candidate] = field(default_factory=list)     # strongest first
    clusters: List[Candidate] = field(default_factory=list)
    posts: List[Post] = field(default_factory=list)

    @property
    def term(self) -> Optional[Candidate]:
        return self.terms[0] if self.terms else None

    @property
    def cluster(self) -> Optional[Candidate]:
        """The trend's main cluster: the highest-scoring one, if any."""
        return max(self.clusters, key=lambda c: c.score) if self.clusters else None

    @property
    def key(self) -> str:  # stable identity: the lead hashtag if there is one, else the main cluster
        return self.term.key if self.term else self.cluster.key

    @property
    def lead(self) -> Candidate:
        return max(self.terms + self.clusters, key=lambda c: c.score)

    def absorb(self, other: "Trend") -> None:
        self.score = max(self.score, other.score)
        self.terms = sorted(self.terms + other.terms, key=lambda c: -c.score)
        self.clusters += other.clusters
        seen = {p.normalized_id for p in self.posts}
        self.posts += [p for p in other.posts if p.normalized_id not in seen]


def dedupe_windows(rows: Sequence[Tuple]) -> Dict[str, List[Window]]:
    """rows: (ref, start, end, frequency, baseline, z). Keeps one row per
    (ref, window); retries upstream can write the same window twice."""
    by: Dict[str, Dict[datetime, Window]] = {}
    for ref, start, end, freq, base, z in rows:
        by.setdefault(ref, {})[start] = Window(start, end, int(freq), float(base or 0), float(z or 0))
    return {ref: sorted(w.values(), key=lambda w: w.start) for ref, w in by.items()}


def score_signals(kind: str, windows: Dict[str, List[Window]], now: datetime, decay_per_hour: float,
                  min_z: float) -> List[Candidate]:
    out = []
    for ref, ws in windows.items():
        best, best_score, best_age = None, None, 0.0
        for w in ws:
            age = max(0.0, (now - w.end) / timedelta(hours=1))
            s = w.z * (decay_per_hour ** age)
            if best_score is None or s > best_score:
                best, best_score, best_age = w, s, age
        if best is not None and best.z >= min_z:
            out.append(Candidate(kind, ref, round(best_score, 2), best, round(best_age, 2), ws))
    return out


def tag_share(posts: Sequence[Post], tag: str) -> float:
    if not posts:
        return 0.0
    tag = tag.lower().lstrip("#")
    hits = sum(tag in {t.lower() for t in HASHTAG.findall(p.text)} for p in posts)
    return hits / len(posts)


def merge(terms: List[Candidate], clusters: List[Candidate], posts: Dict[str, List[Post]],
          merge_share: float = 0.3, overlap: float = 0.5) -> List[Trend]:
    """Combines signals that describe the same trend.

    1. Each cluster joins the hashtag most prevalent in it (if in >= merge_share
       of its posts). One trend often splits into a few clusters (different
       phrasings) that all carry its tag.
    2. Then any two trends whose posts largely overlap (shared posts >= `overlap`
       of the smaller set) are one trend, e.g. #lowercase and #brandvoice
       tagging the same posts. The stronger one absorbs the weaker.
    """
    by_term: Dict[str, List[Candidate]] = {}
    unclaimed: List[Candidate] = []
    for c in clusters:
        shares = [(tag_share(posts.get(c.key, []), t.ref), t) for t in terms]
        best = max(shares, key=lambda st: st[0], default=(0.0, None))
        if best[1] is not None and best[0] >= merge_share:
            by_term.setdefault(best[1].key, []).append(c)
        else:
            unclaimed.append(c)
    trends: List[Trend] = []
    for t in terms:
        absorbed = by_term.get(t.key, [])
        trend = Trend(t.score, terms=[t], posts=list(posts.get(t.key, [])))
        for c in absorbed:
            trend.absorb(Trend(c.score, clusters=[c], posts=list(posts.get(c.key, []))))
        trends.append(trend)
    trends += [Trend(c.score, clusters=[c], posts=list(posts.get(c.key, []))) for c in unclaimed]

    kept: List[Trend] = []
    for trend in sorted(trends, key=lambda t: (-t.score, t.key)):
        ids = {p.normalized_id for p in trend.posts}
        for k in kept:
            kids = {p.normalized_id for p in k.posts}
            if ids and kids and len(ids & kids) >= overlap * min(len(ids), len(kids)):
                k.absorb(trend)
                break
        else:
            kept.append(trend)
    return kept


def top(trends: List[Trend], n: int) -> List[Trend]:
    return sorted(trends, key=lambda t: (-t.score, t.key))[:n]
