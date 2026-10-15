"""Phase 3 IT against the real te-infra (Postgres + Redis + RabbitMQ).

The check: "run one aggregation cycle and confirm trend_ranking is populated
and the Redis :current pointer resolves to a matching versioned key", plus
the trigger contract over a real broker.

Self-contained: it seeds its own posts and scores under a unique hashtag, with
timestamps in 2099 so they're the newest data (the cycle's "now"), then
removes them and re-runs a cycle so the ranking goes back to real data.
"""
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import pika
import psycopg2

from aggregator import cache
from aggregator.config import load_config
from aggregator.consumer import Consumer, Signals, connection_params
from aggregator.cycle import Aggregator
from aggregator.lock import SingleFlightLock
from aggregator.redis_client import Redis
from aggregator.repository import Repository
from aggregator.synthesis import trend_id
from aggregator.trigger import POLL, SPIKE, TriggerState

CFG = load_config()
FUTURE = datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)


def pg():
    return psycopg2.connect(host=CFG.postgres_host, port=CFG.postgres_port, user=CFG.postgres_user,
                            password=CFG.postgres_password, dbname=CFG.postgres_db)


class CycleIT(unittest.TestCase):
    def setUp(self):
        self.tag = f"#it{uuid.uuid4().hex[:8]}"
        self.db = pg()
        self.redis = Redis(CFG.redis_host, CFG.redis_port)
        self.agg = Aggregator(Repository(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user,
                                         CFG.postgres_password, CFG.postgres_db),
                              self.redis, SingleFlightLock(self.redis), top_n=CFG.top_n)
        with self.db, self.db.cursor() as cur:
            cur.execute("INSERT INTO source (source_key, source_type) VALUES (%s, 'api') RETURNING id",
                        (f"src_tiktok_it_{self.tag[1:]}",))
            self.source_id = cur.fetchone()[0]
            for i in range(6):
                text = f"everyone is doing the {self.tag} thing now #fyp take {i}"
                cur.execute("INSERT INTO raw_content_item (source_id, external_content_id, raw_text, posted_at,"
                            " engagement_likes) VALUES (%s, %s, %s, %s, %s) RETURNING id, ingested_at",
                            (self.source_id, f"{self.tag}-{i}", text, FUTURE - timedelta(minutes=10 * i), 100 * i))
                rid, ing = cur.fetchone()
                cur.execute("INSERT INTO normalized_content_item (raw_content_id, raw_content_ingested_at,"
                            " cleaned_text, dedup_hash, is_relevant, category) VALUES (%s, %s, %s, %s, TRUE, 'social')",
                            (rid, ing, text, f"{self.tag}-{i}"))
            cur.execute("INSERT INTO term_frequency_snapshot (term, window_start, window_end, frequency,"
                        " baseline_frequency, z_score) VALUES (%s, %s, %s, 6, 0.5, 8.5)",
                        (self.tag, FUTURE - timedelta(hours=1), FUTURE))

    def tearDown(self):
        with self.db, self.db.cursor() as cur:
            cur.execute("DELETE FROM term_frequency_snapshot WHERE term = %s", (self.tag,))
            cur.execute("DELETE FROM normalized_content_item WHERE dedup_hash LIKE %s", (f"{self.tag}-%",))
            cur.execute("DELETE FROM raw_content_item WHERE source_id = %s", (self.source_id,))
            cur.execute("DELETE FROM source WHERE id = %s", (self.source_id,))
            cur.execute("DELETE FROM trend_ranking WHERE signal_key = %s", (f"term:{self.tag}",))
        self.run_cycle()  # ranking and cache back to real data
        self.db.close()
        self.redis.close()

    def run_cycle(self):
        for _ in range(20):  # a running te-aggregator may briefly hold the lock
            trends = self.agg.run("integration-test")
            if trends is not None:
                return trends
            time.sleep(0.5)
        self.fail("could not acquire the aggregation lock")

    def test_cycle_populates_trend_ranking_and_redis(self):
        trends = self.run_cycle()
        top = trends[0]
        self.assertEqual((top["name"], top["rank"], top["trendId"]), (self.tag, 1, trend_id(f"term:{self.tag}")))
        self.assertEqual((top["sources"], top["category"], top["velocityLabel"]), (["tiktok"], "social", "spike"))
        self.assertEqual(len(top["samplePosts"]), 3)

        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT rank, label, detail->>'trendId' FROM trend_ranking WHERE id = %s", (top["trendId"],))
            self.assertEqual(cur.fetchone(), (1, self.tag, top["trendId"]))
            cur.execute("SELECT count(*) FROM trend_ranking WHERE rank IS NOT NULL")
            self.assertEqual(cur.fetchone()[0], len(trends))

        version = self.redis.get(cache.CURRENT_KEY).decode()
        payload = json.loads(self.redis.get(cache.VERSION_KEY.format(version)))
        self.assertEqual(payload["trends"][0]["trendId"], top["trendId"])
        self.assertGreater(self.redis.ttl(cache.VERSION_KEY.format(version)), 0)

    def test_same_trend_keeps_its_id_across_cycles(self):
        first, second = self.run_cycle(), self.run_cycle()
        self.assertEqual(first[0]["trendId"], second[0]["trendId"])


class TriggerIT(unittest.TestCase):
    """The trigger contract over the real broker, on throwaway queues bound to
    the real exchange (so a running te-aggregator isn't starved)."""

    def setUp(self):
        self.params = connection_params(CFG.rabbitmq_host, CFG.rabbitmq_port, CFG.rabbitmq_user, CFG.rabbitmq_pass)
        self.conn = pika.BlockingConnection(self.params)
        self.ch = self.conn.channel()
        rid = uuid.uuid4().hex[:8]
        self.poll_q, self.score_q = f"q.it.agg.poll.{rid}", f"q.it.agg.score.{rid}"
        for q, keys in ((self.poll_q, ["source.poll.completed"]),
                        (self.score_q, ["trend.score.upserted", "cluster.score.upserted"])):
            self.ch.queue_declare(queue=q, durable=False, auto_delete=False)
            for k in keys:
                self.ch.queue_bind(queue=q, exchange="trends.topic", routing_key=k)

    def tearDown(self):
        if self.conn.is_open:
            self.ch.queue_delete(queue=self.poll_q)
            self.ch.queue_delete(queue=self.score_q)
            self.conn.close()

    def publish(self, key, msg):
        self.ch.basic_publish(exchange="trends.topic", routing_key=key, body=json.dumps(msg).encode())

    def consumer(self, runs, debounce=1.0):
        trigger = TriggerState(fallback_s=3600, debounce_s=debounce, max_wait_s=10, started_at=time.monotonic())
        return Consumer(self.params, self.poll_q, self.score_q, Signals(trigger), runs.append)

    def test_spike_runs_immediately(self):
        runs = []
        self.publish("cluster.score.upserted", {"clusterId": "it", "velocityLabel": "spike"})
        started = time.monotonic()
        self.consumer(runs, debounce=30).run(max_cycles=1, max_seconds=10)
        self.assertEqual(runs, [SPIKE])
        self.assertLess(time.monotonic() - started, 5)  # didn't wait out the 30 s debounce

    def test_burst_of_poll_notices_runs_once(self):
        runs = []
        for i in range(5):
            self.publish("source.poll.completed", {"sourceId": "it", "pollCycleId": f"pc_it_{i}", "itemCount": 1})
        self.publish("trend.score.upserted", {"term": "#it", "velocityLabel": "rising"})  # not a spike: no run
        self.consumer(runs, debounce=1.0).run(max_seconds=4)
        self.assertEqual(runs, [POLL])


if __name__ == "__main__":
    unittest.main()
