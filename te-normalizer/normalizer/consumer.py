"""NORM-1 (consumer) + NORM-6 (publisher). One pika BlockingConnection and
one channel. content.normalized is published on the same channel the message
was consumed from, then the input message is acked. That order gives
at-least-once delivery: a crash before the ack redelivers the input.
"""
import json
import logging
import time
from collections import Counter
from typing import Callable, Optional

import pika

from normalizer.pipeline import INVALID, InvalidMessage

logger = logging.getLogger(__name__)


class ChannelPublisher:
    def __init__(self, channel, exchange: str):
        self._channel = channel
        self._exchange = exchange

    def publish(self, routing_key: str, message: dict) -> None:
        self._channel.basic_publish(
            exchange=self._exchange,
            routing_key=routing_key,
            body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )


def connection_params(host, port, user, password) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(
        host=host, port=port, credentials=pika.PlainCredentials(user, password), heartbeat=60,
    )


class Consumer:
    """Consumes `queue`, calling handler(body, publisher) -> outcome for each message.
    handler raising InvalidMessage -> ack (drop). Any other exception -> nack +
    requeue, and back off briefly so a down database doesn't spin the CPU."""

    def __init__(self, params: pika.ConnectionParameters, queue: str, exchange: str, prefetch: int,
                 handler: Callable, log_every: int = 100, retry_backoff_seconds: float = 2.0):
        self._params = params
        self._queue = queue
        self._exchange = exchange
        self._prefetch = prefetch
        self._handler = handler
        self._log_every = log_every
        self._retry_backoff_seconds = retry_backoff_seconds
        self.counts: Counter = Counter()

    def run(self, max_messages: Optional[int] = None, idle_timeout: Optional[float] = None) -> Counter:
        """Consumes until stopped. max_messages / idle_timeout (seconds with no
        message) end the loop early. Tests use them; the service passes neither."""
        connection = pika.BlockingConnection(self._params)
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=self._prefetch)
            publisher = ChannelPublisher(channel, self._exchange)
            logger.info("consuming %s (prefetch %d)", self._queue, self._prefetch)
            handled = 0
            for method, _props, body in channel.consume(self._queue, inactivity_timeout=idle_timeout):
                if method is None:  # inactivity_timeout elapsed
                    break
                self._handle_one(channel, publisher, method, body)
                handled += 1
                if max_messages is not None and handled >= max_messages:
                    break
            channel.cancel()
        finally:
            if connection.is_open:
                connection.close()
        return self.counts

    def _handle_one(self, channel, publisher, method, body) -> None:
        try:
            outcome = self._handler(body, publisher)
        except InvalidMessage as exc:
            logger.warning("dropping invalid message: %s", exc)
            channel.basic_ack(method.delivery_tag)
            self._count(INVALID)
            return
        except Exception:
            logger.exception("processing failed; requeueing")
            channel.basic_nack(method.delivery_tag, requeue=True)
            self._count("retried")
            time.sleep(self._retry_backoff_seconds)
            return
        channel.basic_ack(method.delivery_tag)
        self._count(outcome)

    def _count(self, outcome: str) -> None:
        self.counts[outcome] += 1
        total = sum(self.counts.values())
        if total % self._log_every == 0:
            logger.info("processed %d: %s", total, dict(self.counts))
