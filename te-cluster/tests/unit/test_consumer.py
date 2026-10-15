import unittest
from unittest.mock import MagicMock, patch

from cluster.consumer import Consumer
from cluster.handler import BatchResult


def method(tag):
    m = MagicMock()
    m.delivery_tag = tag
    return m


def ok(n_invalid=0):
    return BatchResult({}, n_invalid, [], [])


class TestBatchingConsumer(unittest.TestCase):
    def make(self, handler, batch_size=3):
        return Consumer(params=None, queue="q", exchange="x", handler=handler, batch_size=batch_size,
                        batch_wait_ms=10, idle_flush_seconds=0.0, retry_backoff_seconds=0)

    @patch("cluster.consumer.pika.BlockingConnection")
    def test_full_batches_and_partial_flush_on_timeout(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(method(i), None, b"m") for i in range(1, 5)] + [(None, None, None)])
        handler = MagicMock()
        handler.process_batch.return_value = ok()
        counts = self.make(handler).run(max_messages=4)
        self.assertEqual([len(c.args[0]) for c in handler.process_batch.call_args_list], [3, 1])
        ch.basic_ack.assert_any_call(3, multiple=True)
        ch.basic_ack.assert_any_call(4, multiple=True)
        self.assertEqual(counts["processed"], 4)

    @patch("cluster.consumer.pika.BlockingConnection")
    def test_failed_batch_is_nacked_as_a_whole(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(method(1), None, b"a"), (method(2), None, b"b"), (None, None, None),
                                        (None, None, None)])
        handler = MagicMock()
        handler.process_batch.side_effect = RuntimeError("db down")
        with self.assertLogs("cluster.consumer", level="ERROR"):
            self.make(handler).run(stop_after_idle=True)
        ch.basic_nack.assert_called_once_with(2, multiple=True, requeue=True)
        ch.basic_ack.assert_not_called()

    @patch("cluster.consumer.pika.BlockingConnection")
    def test_idle_flush(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(None, None, None)])
        handler = MagicMock()
        counts = self.make(handler).run(stop_after_idle=True)
        handler.on_idle.assert_called_once()
        self.assertEqual(counts["idle_flushes"], 1)


if __name__ == "__main__":
    unittest.main()
