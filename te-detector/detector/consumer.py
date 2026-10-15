"""DET-1: RabbitMQ consumer. One pika BlockingConnection; trend.score.upserted
is published on the same channel the input was consumed from. When the queue
has been quiet for idle_flush_seconds, the handler's on_idle() closes the
oldest open window, so a replay's last window still gets scored."""
import json
import logging
import time
from collections import Counter
from typing import Optional

import pika

from detector.handler import InvalidMessage

logger = logging.getLogger(__name__)


class ChannelPublisher:
    def __init__(self, channel, exchange: str):
        self._channel = channel
        self._exchange = exchange

    def publish(self, routing_key: str, message: dict) -> None:
        self._channel.basic_publish(
            exchange=self._exchange, routing_key=routing_key,
            body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )


def connection_params(host, port, user, password) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(host=host, port=port, credentials=pika.PlainCredentials(user, password),
                                     heartbeat=60)


class Consumer:
    def __init__(self, params, queue: str, exchange: str, prefetch: int, handler,
                 idle_flush_seconds: float = 120.0, retry_backoff_seconds: float = 2.0):
        self._params = params
        self._queue = queue
        self._exchange = exchange
        self._prefetch = prefetch
        self._handler = handler
        self._idle = idle_flush_seconds
        self._retry_backoff_seconds = retry_backoff_seconds
        self.counts: Counter = Counter()

    def run(self, max_messages: Optional[int] = None, stop_after_idle: bool = False) -> Counter:
        """Consumes until stopped. Tests pass max_messages and/or
        stop_after_idle (return after the first idle flush); the service passes neither."""
        connection = pika.BlockingConnection(self._params)
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=self._prefetch)
            self._handler.publisher = ChannelPublisher(channel, self._exchange)
            logger.info("consuming %s (prefetch %d, idle flush %.0fs)", self._queue, self._prefetch, self._idle)
            handled = 0
            for method, _props, body in channel.consume(self._queue, inactivity_timeout=self._idle):
                if method is None:  # quiet for idle_flush_seconds
                    self._handler.on_idle()
                    self.counts["idle_flushes"] += 1
                    if stop_after_idle:
                        break
                    continue
                self._handle_one(channel, method, body)
                handled += 1
                if max_messages is not None and handled >= max_messages:
                    break
            channel.cancel()
        finally:
            if connection.is_open:
                connection.close()
        return self.counts

    def _handle_one(self, channel, method, body) -> None:
        try:
            self._handler.on_message(body)
        except InvalidMessage as exc:
            logger.warning("dropping invalid message: %s", exc)
            channel.basic_ack(method.delivery_tag)
            self.counts["invalid"] += 1
            return
        except Exception:
            logger.exception("processing failed; requeueing")
            channel.basic_nack(method.delivery_tag, requeue=True)
            self.counts["retried"] += 1
            time.sleep(self._retry_backoff_seconds)
            return
        channel.basic_ack(method.delivery_tag)
        self.counts["processed"] += 1
