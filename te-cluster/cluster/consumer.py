"""CLU-1: micro-batching RabbitMQ consumer (design doc: up to 64 messages or
a 50ms flush window, whichever comes first, then one embedding forward pass
for the whole batch). The batch is acked in one go after it's persisted, or
nacked and requeued if processing failed. After idle_flush_seconds with no
messages, the handler's on_idle() closes the oldest growth window."""
import json
import logging
import time
from collections import Counter
from typing import Optional

import pika

logger = logging.getLogger(__name__)


class ChannelPublisher:
    def __init__(self, channel, exchange: str):
        self._channel, self._exchange = channel, exchange

    def publish(self, routing_key: str, message: dict) -> None:
        self._channel.basic_publish(
            exchange=self._exchange, routing_key=routing_key, body=json.dumps(message).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))


def connection_params(host, port, user, password) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(host=host, port=port, credentials=pika.PlainCredentials(user, password),
                                     heartbeat=120)  # first batch can be slow while the model warms up


class Consumer:
    def __init__(self, params, queue: str, exchange: str, handler, batch_size: int = 64,
                 batch_wait_ms: int = 50, idle_flush_seconds: float = 120.0, retry_backoff_seconds: float = 2.0):
        self._params, self._queue, self._exchange, self._handler = params, queue, exchange, handler
        self._batch_size = batch_size
        self._wait = batch_wait_ms / 1000.0
        self._idle = idle_flush_seconds
        self._retry_backoff_seconds = retry_backoff_seconds
        self.counts: Counter = Counter()

    def run(self, max_messages: Optional[int] = None, stop_after_idle: bool = False) -> Counter:
        connection = pika.BlockingConnection(self._params)
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=self._batch_size * 2)
            self._handler.publisher = ChannelPublisher(channel, self._exchange)
            logger.info("consuming %s (batch %d / %.0fms, idle flush %.0fs)", self._queue, self._batch_size,
                        self._wait * 1000, self._idle)
            batch, last_message, handled = [], time.monotonic(), 0
            for method, _props, body in channel.consume(self._queue, inactivity_timeout=self._wait):
                if method is not None:
                    batch.append((method, body))
                    last_message = time.monotonic()
                    if len(batch) < self._batch_size and (max_messages is None or handled + len(batch) < max_messages):
                        continue
                if batch:  # full, or the flush window elapsed with something waiting
                    handled += self._process(channel, batch)
                    batch = []
                    if max_messages is not None and handled >= max_messages:
                        break
                elif time.monotonic() - last_message >= self._idle:
                    self._handler.on_idle()
                    self.counts["idle_flushes"] += 1
                    last_message = time.monotonic()
                    if stop_after_idle:
                        break
            channel.cancel()
        finally:
            if connection.is_open:
                connection.close()
        return self.counts

    def _process(self, channel, batch) -> int:
        last_tag = batch[-1][0].delivery_tag
        try:
            result = self._handler.process_batch([body for _, body in batch])
        except Exception:
            logger.exception("batch of %d failed; requeueing", len(batch))
            channel.basic_nack(last_tag, multiple=True, requeue=True)
            self.counts["retried"] += len(batch)
            time.sleep(self._retry_backoff_seconds)
            return 0
        channel.basic_ack(last_tag, multiple=True)
        self.counts["processed"] += len(batch) - result.invalid
        self.counts["invalid"] += result.invalid
        self.counts["batches"] += 1
        return len(batch)
