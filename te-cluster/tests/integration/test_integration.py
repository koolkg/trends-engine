"""Phase 2b IT: real model, real Postgres, real RabbitMQ (te-infra up).
The check: "feed 2-3 differently-worded posts about the same thing, confirm
they land in one cluster, and an unrelated post seeds a new one."

Self-contained: it creates its own normalized_content_item rows (the
assignment table FKs them) under a per-run source, consumes from a throwaway
queue, and only looks at its own rows and messages.
"""
import json
import time
import unittest
import uuid
from datetime import timedelta

import pika
import psycopg2

from cluster.centroids import CentroidIndex
from cluster.config import load_config
from cluster.consumer import Consumer, connection_params
from cluster.embedder import MiniLMEmbedder
from cluster.handler import Handler
from cluster.store import Store
from cluster.velocity import VelocityTracker

CFG = load_config()
SCORES_Q = "q.aggregator.trend-score-upserted"  # cluster.score.upserted is bound here too (te-infra)
SAME_IDEA = [
    "brands are typing all their captions in lowercase now",
    "every brand is suddenly writing its captions in all lowercase",
    "so many brand accounts posting lowercase captions lately",
]
UNRELATED = "my sourdough starter died again this morning"


class ClusterIT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.embedder = MiniLMEmbedder(CFG.model_name)

    def setUp(self):
        self.run_id = uuid.uuid4().hex[:8]
        self.params = connection_params(CFG.rabbitmq_host, CFG.rabbitmq_port, CFG.rabbitmq_user, CFG.rabbitmq_pass)
        self.conn = pika.BlockingConnection(self.params)
        self.ch = self.conn.channel()
        self.queue = f"q.it.cluster.{self.run_id}"
        self.ch.queue_declare(queue=self.queue, durable=False, auto_delete=False)
        self.db = psycopg2.connect(host=CFG.postgres_host, port=CFG.postgres_port, user=CFG.postgres_user,
                                   password=CFG.postgres_password, dbname=CFG.postgres_db)
        self.store = Store(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user, CFG.postgres_password,
                           CFG.postgres_db)

    def tearDown(self):
        if self.conn.is_open:
            self.ch.queue_delete(queue=self.queue)
            self.conn.close()
        self.store.close()
        self.db.close()

    def normalized_rows(self, texts):
        """Creates raw + normalized rows (as te-normalizer would) and returns their ids."""
        ids = []
        with self.db, self.db.cursor() as cur:
            cur.execute("INSERT INTO source (source_key, source_type) VALUES (%s, 'api') RETURNING id",
                        (f"src_it_cluster_{self.run_id}",))
            sid = cur.fetchone()[0]
            for i, text in enumerate(texts):
                cur.execute("INSERT INTO raw_content_item (source_id, external_content_id, raw_text)"
                            " VALUES (%s, %s, %s) RETURNING id, ingested_at", (sid, f"{self.run_id}-{i}", text))
                rid, ingested = cur.fetchone()
                cur.execute("INSERT INTO normalized_content_item (raw_content_id, raw_content_ingested_at,"
                            " cleaned_text, dedup_hash, is_relevant, category) VALUES (%s, %s, %s, %s, TRUE, 'social')"
                            " RETURNING id", (rid, ingested, text, f"it-{self.run_id}-{i}"))
                ids.append(cur.fetchone()[0])
        return ids

    def cluster_of(self, normalized_id):
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT cluster_id::text FROM content_cluster_assignment WHERE content_id = %s",
                        (normalized_id,))
            rows = cur.fetchall()
        self.assertEqual(len(rows), 1, f"post {normalized_id} should be in exactly one cluster")
        return rows[0][0]

    def run_consumer(self, bodies, handler, **run_kwargs):
        for body in bodies:
            self.ch.basic_publish(exchange="", routing_key=self.queue, body=body)
        return Consumer(self.params, self.queue, CFG.exchange, handler, batch_size=CFG.batch_size,
                        batch_wait_ms=200, idle_flush_seconds=2.0).run(**run_kwargs)

    def body(self, nid, text, posted):
        return json.dumps({"normalizedId": nid, "cleanedText": text, "postedAt": posted}).encode()

    def handler(self, **velocity):
        index = CentroidIndex(self.embedder.dim, CFG.similarity_threshold, timedelta(hours=CFG.evict_after_hours))
        h = Handler(self.embedder, index, VelocityTracker(**velocity), self.store)
        h.load()  # starts from whatever centroids already exist, as the service does
        return h

    def test_paraphrases_share_a_cluster_and_unrelated_post_seeds_a_new_one(self):
        ids = self.normalized_rows(SAME_IDEA + [UNRELATED])
        bodies = [self.body(nid, text, "2026-09-07T10:00:00Z") for nid, text in zip(ids, SAME_IDEA + [UNRELATED])]
        counts = self.run_consumer(bodies, self.handler(), max_messages=len(bodies))
        self.assertEqual(counts["processed"], 4)
        same = {self.cluster_of(nid) for nid in ids[:3]}
        self.assertEqual(len(same), 1, "the three paraphrases should share one cluster")
        self.assertNotIn(self.cluster_of(ids[3]), same)
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT member_count FROM cluster_centroid WHERE cluster_id = %s", (same.pop(),))
            self.assertGreaterEqual(cur.fetchone()[0], 3)

    def test_growth_window_is_scored_and_announced(self):
        texts = SAME_IDEA + SAME_IDEA[:1]
        ids = self.normalized_rows(texts)
        bodies = [self.body(nid, t, f"2026-09-07T11:{10 + i:02d}:00Z") for i, (nid, t) in enumerate(zip(ids, texts))]
        h = self.handler(warmup_windows=0, min_count=2)
        counts = self.run_consumer(bodies, h, stop_after_idle=True)  # idle flush closes the 11:00 window
        self.assertEqual(counts["idle_flushes"], 1)
        cid = self.cluster_of(ids[0])
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT frequency FROM cluster_velocity_snapshot WHERE cluster_id = %s", (cid,))
            self.assertEqual([r[0] for r in cur.fetchall()], [4])
        got, deadline = [], time.time() + 3
        while time.time() < deadline and not got:
            method, _, raw = self.ch.basic_get(queue=SCORES_Q, auto_ack=False)
            if method is None:
                time.sleep(0.05)
                continue
            msg = json.loads(raw)
            if msg.get("clusterId") == cid:
                self.ch.basic_ack(method.delivery_tag)
                got.append(msg)
            else:
                self.ch.basic_nack(method.delivery_tag, requeue=True)
                break
        self.assertEqual([m["frequency"] for m in got], [4])


if __name__ == "__main__":
    unittest.main()
