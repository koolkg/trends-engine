import unittest
from unittest.mock import MagicMock, patch

from detector.consumer import Consumer
from detector.handler import InvalidMessage


def method(tag):
    m = MagicMock()
    m.delivery_tag = tag
    return m


class TestConsumer(unittest.TestCase):
    def make(self, handler):
        return Consumer(params=None, queue="q", exchange="x", prefetch=10, handler=handler,
                        idle_flush_seconds=0.1, retry_backoff_seconds=0)

    def test_success_acks(self):
        handler, ch = MagicMock(), MagicMock()
        c = self.make(handler)
        c._handle_one(ch, method(1), b"{}")
        ch.basic_ack.assert_called_once_with(1)

    def test_invalid_is_acked_and_dropped(self):
        handler, ch = MagicMock(), MagicMock()
        handler.on_message.side_effect = InvalidMessage("bad")
        c = self.make(handler)
        c._handle_one(ch, method(2), b"x")
        ch.basic_ack.assert_called_once_with(2)
        self.assertEqual(c.counts["invalid"], 1)

    def test_other_errors_requeue(self):
        handler, ch = MagicMock(), MagicMock()
        handler.on_message.side_effect = RuntimeError("db down")
        c = self.make(handler)
        with self.assertLogs("detector.consumer", level="ERROR"):
            c._handle_one(ch, method(3), b"x")
        ch.basic_nack.assert_called_once_with(3, requeue=True)
        ch.basic_ack.assert_not_called()

    @patch("detector.consumer.pika.BlockingConnection")
    def test_idle_triggers_flush_and_can_stop_the_loop(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(method(1), None, b"a"), (None, None, None), (method(2), None, b"b")])
        handler = MagicMock()
        counts = self.make(handler).run(stop_after_idle=True)
        handler.on_idle.assert_called_once()
        self.assertEqual((counts["processed"], counts["idle_flushes"]), (1, 1))
        self.assertIsNotNone(handler.publisher)  # consumer wires its channel publisher in

    @patch("detector.consumer.pika.BlockingConnection")
    def test_idle_does_not_stop_the_service_loop(self, conn_cls):
        ch = conn_cls.return_value.channel.return_value
        ch.consume.return_value = iter([(None, None, None), (method(1), None, b"a")])
        counts = self.make(MagicMock()).run(max_messages=1)
        self.assertEqual((counts["idle_flushes"], counts["processed"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
