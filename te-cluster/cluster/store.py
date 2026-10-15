"""CLU-6: Postgres writes. Each micro-batch commits in one transaction:
- content_cluster_assignment: one row per post (ON CONFLICT DO NOTHING, so a
  redelivered message doesn't duplicate it),
- cluster_centroid: upsert of every cluster the batch touched (one row per
  active cluster),
- cluster_centroid: delete of clusters evicted as cold (their assignment
  history stays),
- cluster_velocity_snapshot: one row per emitted growth score.
Centroids are also read back at startup, so the index survives a restart.
"""
import logging
from datetime import datetime
from typing import Iterable, List, Sequence, Tuple

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

from cluster.centroids import Cluster

logger = logging.getLogger(__name__)

Z_LIMIT = 9999.99
BASELINE_LIMIT = 99999999.99


def to_pgvector(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"


def from_pgvector(text: str) -> np.ndarray:
    return np.array([float(x) for x in text.strip("[]").split(",")], dtype=np.float32)


def _clamp(x: float, limit: float) -> float:
    return max(-limit, min(limit, x))


class Store:
    def __init__(self, host, port, user, password, dbname):
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None

    def connect(self):
        self._conn = psycopg2.connect(**self._dsn)
        logger.info("connected to postgres at %s:%s/%s", self._dsn["host"], self._dsn["port"], self._dsn["dbname"])

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure(self):
        if self._conn is None:
            self.connect()

    def load_centroids(self) -> List[Cluster]:
        self._ensure()
        with self._conn, self._conn.cursor() as cur:
            cur.execute("SELECT cluster_id::text, centroid::text, member_count, last_assigned_at FROM cluster_centroid")
            return [Cluster(cid, from_pgvector(vec), int(n), last) for cid, vec, n, last in cur.fetchall()]

    def already_assigned(self, normalized_ids: Sequence[int]) -> set:
        """Posts that already have a cluster, e.g. a batch redelivered after a crash
        between commit and ack. Skipping them keeps a post in exactly one
        cluster and keeps growth counts honest."""
        if not normalized_ids:
            return set()
        self._ensure()
        with self._conn, self._conn.cursor() as cur:
            cur.execute("SELECT DISTINCT content_id FROM content_cluster_assignment WHERE content_id = ANY(%s)",
                        (list(normalized_ids),))
            return {row[0] for row in cur.fetchall()}

    def save_batch(self, assignments: Sequence[Tuple[int, str, float]], touched: Iterable[Cluster],
                   evicted: Sequence[str], scores: Sequence) -> None:
        try:
            self._save_batch(assignments, list(touched), evicted, scores)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("postgres connection lost, reconnecting and retrying once")
            self.close()
            self._save_batch(assignments, list(touched), evicted, scores)

    def _save_batch(self, assignments, touched: List[Cluster], evicted, scores) -> None:
        self._ensure()
        with self._conn, self._conn.cursor() as cur:  # one transaction per micro-batch
            if touched:  # centroids first: assignments don't FK them, but keep the order readable
                execute_values(
                    cur,
                    "INSERT INTO cluster_centroid (cluster_id, centroid, member_count, last_assigned_at) VALUES %s"
                    " ON CONFLICT (cluster_id) DO UPDATE SET centroid = EXCLUDED.centroid,"
                    " member_count = EXCLUDED.member_count, last_assigned_at = EXCLUDED.last_assigned_at",
                    [(c.cluster_id, to_pgvector(c.centroid), c.member_count, c.last_assigned_at) for c in touched],
                    template="(%s, %s::vector, %s, %s)",
                )
            if assignments:
                execute_values(
                    cur,
                    "INSERT INTO content_cluster_assignment (content_id, cluster_id, similarity_score) VALUES %s"
                    " ON CONFLICT (content_id, cluster_id) DO NOTHING",
                    [(nid, cid, round(min(1.0, max(-1.0, sim)), 3)) for nid, cid, sim in assignments],
                )
            if evicted:
                cur.execute("DELETE FROM cluster_centroid WHERE cluster_id = ANY(%s::uuid[])", (list(evicted),))
            if scores:
                execute_values(
                    cur,
                    "INSERT INTO cluster_velocity_snapshot (cluster_id, window_start, window_end, frequency,"
                    " baseline_frequency, z_score) VALUES %s",
                    [(s.cluster_id, s.window_start, s.window_end, s.frequency,
                      _clamp(s.baseline_frequency, BASELINE_LIMIT), _clamp(s.z_score, Z_LIMIT)) for s in scores],
                )
