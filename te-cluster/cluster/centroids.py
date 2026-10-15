"""CLU-3 + CLU-4: the in-memory centroid index and the join-or-seed rule.

One row per *active cluster*, not per post (Low Level Details tab): a post's
own vector is used once, to pick its cluster, and then discarded. Lookup is
brute-force cosine similarity over a numpy matrix of unit-length centroids,
which is a single matrix-vector product. At PoC scale (hundreds of
clusters) that beats maintaining an ANN index (CLU-3).

    nearest = argmax(centroids @ v)
    if similarity >= threshold: join it,
        centroid' = normalize(centroid + (v - centroid) / (member_count + 1))
    else: seed a new cluster at v

Clusters with no new member for `evict_after` (by post time) are dropped from
the index (cold eviction). Their assignment history is untouched.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class Cluster:
    cluster_id: str
    centroid: np.ndarray  # unit length
    member_count: int
    last_assigned_at: datetime


@dataclass(frozen=True)
class Assignment:
    cluster_id: str
    similarity: float
    seeded: bool  # True: this post started a new cluster


def _unit(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


class CentroidIndex:
    def __init__(self, dim: int, threshold: float = 0.72, evict_after: timedelta = timedelta(hours=24)):
        self.dim = dim
        self.threshold = threshold
        self.evict_after = evict_after
        self._ids: List[str] = []
        self._matrix = np.zeros((0, dim), dtype=np.float32)
        self.clusters: Dict[str, Cluster] = {}

    def __len__(self) -> int:
        return len(self._ids)

    def load(self, clusters: List[Cluster]) -> None:
        """Rebuilds the index from persisted centroids (restart)."""
        self.clusters = {c.cluster_id: c for c in clusters}
        self._rebuild()

    def _rebuild(self) -> None:
        self._ids = list(self.clusters)
        self._matrix = (np.stack([self.clusters[i].centroid for i in self._ids]).astype(np.float32)
                        if self._ids else np.zeros((0, self.dim), dtype=np.float32))

    def nearest(self, v: np.ndarray) -> Tuple[Optional[str], float]:
        if not self._ids:
            return None, -1.0
        sims = self._matrix @ v
        i = int(np.argmax(sims))
        return self._ids[i], float(sims[i])

    def assign(self, v: np.ndarray, posted_at: datetime) -> Assignment:
        v = _unit(np.asarray(v, dtype=np.float32))
        cluster_id, sim = self.nearest(v)
        if cluster_id is not None and sim >= self.threshold:
            c = self.clusters[cluster_id]
            c.centroid = _unit(c.centroid + (v - c.centroid) / (c.member_count + 1))
            c.member_count += 1
            c.last_assigned_at = max(c.last_assigned_at, posted_at)
            self._matrix[self._ids.index(cluster_id)] = c.centroid
            return Assignment(cluster_id, round(sim, 3), seeded=False)
        new_id = str(uuid.uuid4())
        self.clusters[new_id] = Cluster(new_id, v, 1, posted_at)
        self._ids.append(new_id)
        self._matrix = np.vstack([self._matrix, v[None, :]])
        return Assignment(new_id, 1.0, seeded=True)

    def evict_stale(self, now: datetime) -> List[str]:
        """Drops clusters idle for longer than evict_after (by post time)."""
        cutoff = now - self.evict_after
        stale = [cid for cid, c in self.clusters.items() if c.last_assigned_at < cutoff]
        for cid in stale:
            del self.clusters[cid]
        if stale:
            self._rebuild()
        return stale
