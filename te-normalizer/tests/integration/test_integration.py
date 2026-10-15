"""Phase 1b IT against the real te-infra (Postgres + RabbitMQ). Assumes te-infra
is up (Makefile contract). Self-contained: it publishes its own raw messages,
tagged with a per-run sourceId + token, so it doesn't collide with earlier runs
(dedup is global) or with a te-connector / te-normalizer that happens to be
running against the same broker.

- test_pipeline_against_real_postgres_and_rabbitmq: relevant / irrelevant /
  cross-post duplicate through process() with the real Store and a real
  channel. Checks the DB rows and that only the relevant, non-duplicate
  post lands on BOTH content.normalized queues (detector + cluster).
- test_consumer_loop: the real Consumer on a temporary queue, so it can't
  steal messages from the shared q.normalizer.* queue.
"""
import json
import os
import time
import unittest
import uuid

import pika
import psycopg2

from normalizer.config import load_config
from normalizer.consumer import ChannelPublisher, Consumer, connection_params
from normalizer.pipeline import DUPLICATE, IRRELEVANT, PUBLISHED, process
from normalizer.store import Store

CFG = load_config()
DETECTOR_Q = "q.detector.content-normalized"
CLUSTER_Q = "q.cluster.content-normalized"


def raw(source_id, content_id, text):
    return json.dumps({
        "sourceId": source_id, "sourceType": "api", "contentId": content_id, "rawText": text,
        "author": "@it_test", "postedAt": "2026-09-07T12:00:00Z",
        "engagement": {"likes": 10, "shares": 1, "comments": 2}, "url": "https://example.invalid/x",
    }).encode()


def drain(channel, queue, source_id, timeout_s=3.0):
    """Pull everything currently on `queue`; return only this run's messages.
    Anything else is requeued untouched."""
    mine, deadline = [], time.time() + timeout_s
    while time.time() < deadline:
        method, _, body = channel.basic_get(queue=queue, auto_ack=False)
        if method is None:
            time.sleep(0.05)
            continue
        msg = json.loads(body)
        if msg.get("sourceId") == source_id:
            channel.basic_ack(method.delivery_tag)
            mine.append(msg)
        else:
            channel.basic_nack(method.delivery_tag, requeue=True)
            break  # someone else's message is next; don't spin on it
    return mine


class NormalizerIT(unittest.TestCase):
    def setUp(self):
        self.run_id = uuid.uuid4().hex[:10]
        self.source_id = f"src_it_{self.run_id}"
        self.store = Store(CFG.postgres_host, CFG.postgres_port, CFG.postgres_user,
                           CFG.postgres_password, CFG.postgres_db)
        self.conn = pika.BlockingConnection(connection_params(
            CFG.rabbitmq_host, CFG.rabbitmq_port, CFG.rabbitmq_user, CFG.rabbitmq_pass))
        self.channel = self.conn.channel()
        self.db = psycopg2.connect(host=CFG.postgres_host, port=CFG.postgres_port, user=CFG.postgres_user,
                                   password=CFG.postgres_password, dbname=CFG.postgres_db)

    def tearDown(self):
        self.store.close()
        self.db.close()
        if self.conn.is_open:
            self.conn.close()

    def rows(self):
        with self.db, self.db.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw_content_item r JOIN source s ON s.id = r.source_id "
                        "WHERE s.source_key = %s", (self.source_id,))
            n_raw = cur.fetchone()[0]
            cur.execute("SELECT n.category FROM normalized_content_item n "
                        "JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at "
                        "JOIN source s ON s.id = r.source_id WHERE s.source_key = %s", (self.source_id,))
            return n_raw, [c for (c,) in cur.fetchall()]

    def test_pipeline_against_real_postgres_and_rabbitmq(self):
        pub = ChannelPublisher(self.channel, CFG.exchange)
        token = self.run_id
        relevant = raw(self.source_id, "a", f"found the $12 dupe for the $58 serum, batch {token} #dupe")
        crosspost = raw(self.source_id, "b", f"Found the $12 DUPE for the $58 serum!! batch {token} ✨ #budgetbeauty")
        noise = raw(self.source_id, "c", f"just adopted a very good dog {token}")

        outcomes = [process(body, self.store, pub, CFG.relevance_threshold) for body in (relevant, crosspost, noise)]
        self.assertEqual(outcomes, [PUBLISHED, DUPLICATE, IRRELEVANT])

        n_raw, categories = self.rows()
        self.assertEqual(n_raw, 3)                 # every item archived
        self.assertEqual(categories, ["ecommerce"])  # only the first relevant one normalized

        for queue in (DETECTOR_Q, CLUSTER_Q):     # fan-out: both consumers get their own copy
            with self.subTest(queue=queue):
                got = drain(self.channel, queue, self.source_id)
                self.assertEqual([m["contentId"] for m in got], ["a"])
                self.assertEqual(got[0]["category"], "ecommerce")
                self.assertIsInstance(got[0]["normalizedId"], int)

    def test_consumer_loop(self):
        # A throwaway queue (not exclusive: the Consumer opens its own connection,
        # and exclusive queues are locked to the declaring one).
        queue = f"q.it.normalizer.{self.run_id}"
        self.channel.queue_declare(queue=queue, durable=False, auto_delete=False)
        self.addCleanup(lambda: self.conn.is_open and self.channel.queue_delete(queue=queue))
        bodies = [raw(self.source_id, "c0", f"subject lines with numbers lift our open rate {self.run_id}"),
                  b"not json",
                  raw(self.source_id, "c2", f"my cat knocked my coffee over {self.run_id}")]
        for body in bodies:
            self.channel.basic_publish(exchange="", routing_key=queue, body=body)

        consumer = Consumer(connection_params(CFG.rabbitmq_host, CFG.rabbitmq_port, CFG.rabbitmq_user,
                                              CFG.rabbitmq_pass),
                            queue=queue, exchange=CFG.exchange, prefetch=10,
                            handler=lambda body, p: process(body, self.store, p, CFG.relevance_threshold))
        counts = consumer.run(max_messages=3, idle_timeout=5)

        self.assertEqual((counts["published"], counts["invalid"], counts["irrelevant"]), (1, 1, 1))
        self.assertEqual(self.channel.queue_declare(queue=queue, passive=True).method.message_count, 0)  # all acked
        for q in (DETECTOR_Q, CLUSTER_Q):
            self.assertEqual([m["contentId"] for m in drain(self.channel, q, self.source_id)], ["c0"])

if __name__ == "__main__":
    unittest.main()
