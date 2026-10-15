"""Phase 2a IT against the real te-infra (Postgres + RabbitMQ); assumes it's up.
The check: "feed a burst of content repeating one term, confirm
term_frequency_snapshot rows and a spike event both appear."

Self-contained: messages go to a throwaway queue (so a running te-detector
can't steal them) and use a per-run hashtag (#itXXXXXXXX) so rows and messages
from other runs or services can't be confused with this one's.
"""
import json
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import pika
import psycopg2

from detector.config import load_config
from detector.consumer import Consumer, connection_params
from detector.detector import Detector
from detector.handler import Handler
from detector.store import Store

CFG = load_config()
SCORES_Q = "q.aggregator.trend-score-upserted"
T0 = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def normalized(minutes, text):
    return json.dumps({"normalizedId": 0, "sourceId": "src_it", "cleanedText": text, "category": "social",
                       "postedAt": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")}).encode()


class DetectorIT(unittest.TestCase):
    def setUp(self):
        self.tag = f"#it{uuid.uuid4().hex[:8]}"
        self.queue = f"q.it.detector.{self.tag[1:]}"
        self.params = connection_params(CFG.rabbitmq_host, CFG.rabbitmq_port, CFG.rabbitmq_user, CFG.rabbitmq_pass)
        self.conn = pika.BlockingConnection(self.params)
        self.ch = self.conn.channel()
        self.ch.queue_declare(queue=self.queue, durable=False, auto_delete=False)
        self.store = Store(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user, CFG.postgres_password,
                           CFG.postgres_db)
        self.db = psycopg2.connect(host=CFG.postgres_host, port=CFG.postgres_port, user=CFG.postgres_user,
                                   password=CFG.postgres_password, dbname=CFG.postgres_db)

    def tearDown(self):
        if self.conn.is_open:
            self.ch.queue_delete(queue=self.queue)
            self.conn.close()
        self.store.close()
        self.db.close()

    def publish(self, bodies):
        for body in bodies:
            self.ch.basic_publish(exchange="", routing_key=self.queue, body=body)

    def drain_scores(self, timeout_s=3.0):
        """Messages for this run's tag from the real trend.score.upserted queue; others are requeued."""
        mine, deadline = [], time.time() + timeout_s
        while time.time() < deadline:
            method, _, body = self.ch.basic_get(queue=SCORES_Q, auto_ack=False)
            if method is None:
                time.sleep(0.05)
                continue
            msg = json.loads(body)
            if msg.get("term") == self.tag:
                self.ch.basic_ack(method.delivery_tag)
                mine.append(msg)
            else:
                self.ch.basic_nack(method.delivery_tag, requeue=True)
                break
        return mine

    def rows(self):
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT window_start, frequency, z_score FROM term_frequency_snapshot WHERE term = %s"
                        " ORDER BY window_start", (self.tag,))
            return cur.fetchall()

    def consumer(self, handler, idle=5.0):
        return Consumer(self.params, self.queue, CFG.exchange, prefetch=50, handler=handler,
                        idle_flush_seconds=idle)

    def test_burst_produces_snapshot_rows_and_a_spike_event(self):
        bodies = []
        for hour in range(4):  # quiet background: 2 mentions an hour
            bodies += [normalized(hour * 60 + 5, f"meh {self.tag}"), normalized(hour * 60 + 35, f"meh {self.tag}")]
        bodies += [normalized(4 * 60 + m, f"everyone {self.tag}") for m in range(0, 60, 5)]  # 12 in hour 4
        bodies += [normalized(5 * 60 + 1, "closes hour 4 #unrelatedtag")]
        self.publish(bodies)
        handler = Handler(Detector(window_minutes=60, warmup_windows=1, min_count=2), self.store)
        counts = self.consumer(handler).run(max_messages=len(bodies))
        self.assertEqual(counts["processed"], len(bodies))

        rows = self.rows()
        self.assertEqual([(r[0].hour, r[1]) for r in rows], [(1, 2), (2, 2), (3, 2), (4, 12)])  # hour 0 = warmup
        self.assertGreaterEqual(float(rows[-1][2]), CFG.z_threshold)

        labels = {m["windowStart"]: m["velocityLabel"] for m in self.drain_scores()}
        self.assertEqual(labels["2026-09-07T04:00:00Z"], "spike")
        self.assertNotIn("spike", [v for k, v in labels.items() if k != "2026-09-07T04:00:00Z"])

    def test_idle_flush_scores_the_final_window(self):
        self.publish([normalized(m, f"late night {self.tag}") for m in (1, 2, 3)])
        handler = Handler(Detector(window_minutes=60, warmup_windows=0, min_count=2), self.store)
        counts = self.consumer(handler, idle=1.0).run(stop_after_idle=True)
        self.assertEqual((counts["processed"], counts["idle_flushes"]), (3, 1))
        self.assertEqual([(r[0].hour, r[1]) for r in self.rows()], [(0, 3)])
        self.assertEqual([m["frequency"] for m in self.drain_scores()], [3])


if __name__ == "__main__":
    unittest.main()
