"""Postgres side of a cycle: AGG-2 reads (recent scores and the posts behind
each candidate) and AGG-5 writes (the new ranking, in one transaction)."""
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import execute_values

from aggregator.ranking import Post

logger = logging.getLogger(__name__)

PLATFORMS = (("src_reddit", "reddit"), ("src_x_", "x"), ("src_instagram", "instagram"), ("src_tiktok", "tiktok"))
POST_COLUMNS = ("n.id, n.cleaned_text, n.category, r.posted_at, r.author_handle, s.source_key,"
                " COALESCE(r.engagement_likes,0) + COALESCE(r.engagement_shares,0) + COALESCE(r.engagement_comments,0)")
POST_JOINS = ("FROM normalized_content_item n"
              " JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at"
              " JOIN source s ON s.id = r.source_id")
SCORE_LIMIT = 9999.99


def platform(source_key: str) -> str:
    for prefix, name in PLATFORMS:
        if source_key.startswith(prefix):
            return name
    return source_key


def _post(row) -> Post:
    nid, text, category, posted_at, author, source_key, engagement = row
    return Post(nid, text, category, posted_at, author, platform(source_key), int(engagement))


class Repository:
    def __init__(self, host, port, user, password, dbname):
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = None

    def connect(self):
        self._conn = psycopg2.connect(**self._dsn)

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _cur(self):
        if self._conn is None or self._conn.closed:
            self.connect()
        return self._conn

    def data_now(self) -> Optional[datetime]:
        """Newest scored window end across both signal tables: the cycle's "now"."""
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT GREATEST((SELECT max(window_end) FROM term_frequency_snapshot),"
                        " (SELECT max(window_end) FROM cluster_velocity_snapshot))")
            return cur.fetchone()[0]

    def term_rows(self, since: datetime) -> List[tuple]:
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT term, window_start, window_end, frequency, baseline_frequency, z_score"
                        " FROM term_frequency_snapshot WHERE window_end > %s", (since,))
            return cur.fetchall()

    def cluster_rows(self, since: datetime) -> List[tuple]:
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT cluster_id::text, window_start, window_end, frequency, baseline_frequency, z_score"
                        " FROM cluster_velocity_snapshot WHERE window_end > %s", (since,))
            return cur.fetchall()

    def posts_for_term(self, tag: str, since: datetime, until: datetime) -> List[Post]:
        word = tag.lstrip("#")
        if not word.replace("_", "").isalnum():  # hashtags are \w only; never put anything else in a regex
            return []
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} {POST_JOINS} WHERE r.posted_at > %s AND r.posted_at <= %s"
                        " AND n.cleaned_text ~* %s ORDER BY r.posted_at",
                        (since, until, rf"(^|[^[:alnum:]_#])#{word}([^[:alnum:]_]|$)"))
            return [_post(r) for r in cur.fetchall()]

    def posts_for_cluster(self, cluster_id: str, since: datetime, until: datetime) -> List[Post]:
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT {POST_COLUMNS} {POST_JOINS}"
                        " JOIN content_cluster_assignment a ON a.content_id = n.id"
                        " WHERE a.cluster_id = %s AND r.posted_at > %s AND r.posted_at <= %s ORDER BY r.posted_at",
                        (cluster_id, since, until))
            return [_post(r) for r in cur.fetchall()]

    def write_ranking(self, trends: List[Dict], computed_at: datetime) -> None:
        """AGG-5: one transaction. Readers see the whole old ranking or the whole
        new one. Trends that dropped out keep their row (rank NULL) so campaign
        drafts can still reference them; old unreferenced ones are pruned."""
        conn = self._cur()
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE trend_ranking SET rank = NULL WHERE rank IS NOT NULL")
            if trends:
                execute_values(
                    cur,
                    "INSERT INTO trend_ranking (id, label, category, velocity_score, rank, computed_at, detail,"
                    " signal_key) VALUES %s ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label,"
                    " category = EXCLUDED.category, velocity_score = EXCLUDED.velocity_score,"
                    " rank = EXCLUDED.rank, computed_at = EXCLUDED.computed_at, detail = EXCLUDED.detail,"
                    " signal_key = EXCLUDED.signal_key",
                    [(t["trendId"], t["name"][:500], t["category"],
                      max(-SCORE_LIMIT, min(SCORE_LIMIT, t["velocityScore"])), t["rank"], computed_at,
                      json.dumps(t), t["signalKey"]) for t in trends],
                    template="(%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                )
            cur.execute("DELETE FROM trend_ranking t WHERE t.rank IS NULL AND t.computed_at < now() - interval '7 days'"
                        " AND NOT EXISTS (SELECT 1 FROM campaign_draft_block d WHERE d.trend_id = t.id)")
