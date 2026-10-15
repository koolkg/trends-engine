import unittest
from unittest.mock import MagicMock, patch

from normalizer.consumer import Consumer
from normalizer.pipeline import InvalidMessage


def method(tag):
    m = MagicMock(); m.delivery_tag = tag
    return m


class TestConsumerAckNack(unittest.TestCase):
    def _consumer(self, handler):
        return Consumer(params=None, queue="q", exchange="x", prefetch=10, handler=handler,
                        retry_backoff_seconds=0)

    def test_success_acks(self):
        ch = MagicMock()
        c = self._consumer(lambda body, pub: "published")
        c._handle_one(ch, None, method(1), b"{}")
        ch.basic_ack.assert_called_once_with(1)
        self.assertEqual(c.counts["published"], 1)

    def test_invalid_message_is_acked_and_dropped(self):
        def bad(body, pub):
            raise InvalidMessage("nope")
        ch = MagicMock()
        c = self._consumer(bad)
        c._handle_one(ch, None, method(2), b"x")
        ch.basic_ack.assert_called_once_with(2)
        ch.basic_nack.assert_not_called()
        self.assertEqual(c.counts["invalid"], 1)

    def test_other_errors_are_requeued(self):
        def db_down(body, pub):
            raise RuntimeError("postgres down")
        ch = MagicMock()
        c = self._consumer(db_down)
        with self.assertLogs("normalizer.consumer", level="ERROR"):
            c._handle_one(ch, None, method(3), b"x")
        ch.basic_nack.assert_called_once_with(3, requeue=True)
        ch.basic_ack.assert_not_called()

    @patch("normalizer.consumer.pika.BlockingConnection")
    def test_run_stops_at_max_messages_and_on_idle(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(method(1), None, b"a"), (method(2), None, b"b"), (None, None, None)])
        c = self._consumer(lambda body, pub: "published")
        c.run(max_messages=1)
        self.assertEqual(c.counts["published"], 1)
        ch.consume.return_value = iter([(method(5), None, b"a"), (None, None, None), (method(6), None, b"z")])
        c.run(idle_timeout=0.1)
        self.assertEqual(c.counts["published"], 2)  # stopped at the idle marker, never saw tag 6


if __name__ == "__main__":
    unittest.main()
