"""Phase 1a IT: publish a batch against a REAL RabbitMQ
and assert the right message count lands, with exactly one
source.poll.completed following. Runs in the te-connector container;
`make test-integration` here starts the infra it needs.
"""
import json
import os
import tempfile
import time
import unittest
import uuid

import pika

from connector.config import Config
from connector.main import run

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "trends")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "trends_dev_pw")
EXCHANGE = os.environ.get("RABBITMQ_EXCHANGE", "trends.topic")

# Bound to content.raw.ingested / source.poll.completed per
# te-infra/rabbitmq/definitions.json.
RAW_QUEUE = "q.normalizer.content-raw-ingested"
POLL_QUEUE = "q.aggregator.source-poll-completed"


def _drain(channel, queue, timeout_s=5):
    """basic_get-polls a queue until empty or timeout; returns decoded bodies."""
    messages = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        method, _, body = channel.basic_get(queue=queue, auto_ack=True)
        if method is None:
            time.sleep(0.05)
            continue
        messages.append(json.loads(body.decode()))
    return messages


class TestConnectorAgainstRealRabbitMQ(unittest.TestCase):
    def setUp(self):
        creds = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT, credentials=creds)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        # Drain anything left over from a previous run so this test's counts
        # aren't polluted by stale messages sitting in the queue.
        _drain(self.channel, RAW_QUEUE, timeout_s=1)
        _drain(self.channel, POLL_QUEUE, timeout_s=1)

    def tearDown(self):
        self.connection.close()

    def _run_fixture(self, n):
        """A throwaway fixture whose items carry NO sourceId, so run() stamps
        them with this test's unique source_id. (The real fixture already sets
        sourceId on every line, and run() deliberately never overwrites an
        explicit one — so its messages can't be told apart from a connector
        that happens to be running against the same broker.)"""
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for i in range(n):
                f.write(json.dumps({
                    "sourceType": "api",
                    "contentId": f"it-{uuid.uuid4().hex[:12]}",
                    "rawText": f"integration test post {i}",
                }) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_one_poll_cycle_publishes_batch_then_one_poll_completed(self):
        source_id = f"src_it_test_{uuid.uuid4().hex[:8]}"
        config = Config(
            rabbitmq_host=RABBITMQ_HOST, rabbitmq_port=RABBITMQ_PORT,
            rabbitmq_user=RABBITMQ_USER, rabbitmq_pass=RABBITMQ_PASS, exchange=EXCHANGE,
            fixture_path=self._run_fixture(4),
            source_id=source_id, batch_size=4, poll_interval_seconds=0, loop_fixture=False,
        )

        cycles = run(config=config, max_cycles=1)
        self.assertEqual(cycles, 1)

        raw_messages = _drain(self.channel, RAW_QUEUE, timeout_s=5)
        poll_messages = _drain(self.channel, POLL_QUEUE, timeout_s=5)

        # Only count messages this test run actually produced (in case
        # something else is also publishing against this broker).
        raw_from_this_run = [m for m in raw_messages if m.get("sourceId") == source_id]
        poll_from_this_run = [m for m in poll_messages if m.get("sourceId") == source_id]

        self.assertEqual(len(raw_from_this_run), 4)
        self.assertEqual(len(poll_from_this_run), 1)
        self.assertEqual(poll_from_this_run[0]["itemCount"], 4)
        self.assertIn("pollCycleId", poll_from_this_run[0])
        self.assertIn("completedAt", poll_from_this_run[0])


if __name__ == "__main__":
    unittest.main()
