import unittest
from unittest.mock import MagicMock, patch

import pika

from connector.publisher import Publisher


class TestPublisherConnect(unittest.TestCase):
    def _publisher(self, **kwargs):
        return Publisher(host="h", port=1, user="u", password="p", exchange="trends.topic", **kwargs)

    @patch("connector.publisher.pika.BlockingConnection")
    def test_connect_succeeds_first_try(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        pub = self._publisher()
        pub.connect()
        mock_conn_cls.assert_called_once()
        mock_conn.channel.assert_called_once()

    @patch("connector.publisher.time.sleep", return_value=None)
    @patch("connector.publisher.pika.BlockingConnection")
    def test_connect_retries_then_succeeds(self, mock_conn_cls, mock_sleep):
        mock_conn = MagicMock()
        mock_conn_cls.side_effect = [
            pika.exceptions.AMQPConnectionError("nope"),
            pika.exceptions.AMQPConnectionError("nope"),
            mock_conn,
        ]
        pub = self._publisher(max_connect_retries=5, retry_backoff_seconds=0.01)
        pub.connect()
        self.assertEqual(mock_conn_cls.call_count, 3)

    @patch("connector.publisher.time.sleep", return_value=None)
    @patch("connector.publisher.pika.BlockingConnection")
    def test_connect_gives_up_after_max_retries(self, mock_conn_cls, mock_sleep):
        mock_conn_cls.side_effect = pika.exceptions.AMQPConnectionError("nope")
        pub = self._publisher(max_connect_retries=2, retry_backoff_seconds=0.01)
        with self.assertRaises(pika.exceptions.AMQPConnectionError):
            pub.connect()
        self.assertEqual(mock_conn_cls.call_count, 3)  # initial + 2 retries


class TestPublisherPublish(unittest.TestCase):
    @patch("connector.publisher.pika.BlockingConnection")
    def test_publish_connects_lazily_then_reuses_channel(self, mock_conn_cls):
        mock_channel = MagicMock()
        mock_conn_cls.return_value.channel.return_value = mock_channel
        pub = Publisher(host="h", port=1, user="u", password="p", exchange="trends.topic")

        pub.publish("content.raw.ingested", {"a": 1})
        pub.publish("content.raw.ingested", {"a": 2})

        mock_conn_cls.assert_called_once()  # connected once, reused for both publishes
        self.assertEqual(mock_channel.basic_publish.call_count, 2)
        kwargs = mock_channel.basic_publish.call_args_list[0].kwargs
        self.assertEqual(kwargs["exchange"], "trends.topic")
        self.assertEqual(kwargs["routing_key"], "content.raw.ingested")

    @patch("connector.publisher.pika.BlockingConnection")
    def test_publish_reconnects_and_retries_once_on_dropped_connection(self, mock_conn_cls):
        good_channel = MagicMock()
        bad_channel = MagicMock()
        bad_channel.basic_publish.side_effect = pika.exceptions.StreamLostError("dropped")
        mock_conn_cls.return_value.channel.side_effect = [bad_channel, good_channel]

        pub = Publisher(host="h", port=1, user="u", password="p", exchange="trends.topic")
        pub.publish("content.raw.ingested", {"a": 1})

        bad_channel.basic_publish.assert_called_once()
        good_channel.basic_publish.assert_called_once()

    def test_close_is_a_no_op_when_never_connected(self):
        pub = Publisher(host="h", port=1, user="u", password="p", exchange="trends.topic")
        pub.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
