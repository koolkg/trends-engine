"""CONN-4: a thin pika wrapper — connect, publish, basic retry. No
framework, no connection-pool abstraction; te-connector runs one
long-lived connection for its process lifetime.
"""
import json
import logging
import time

import pika

logger = logging.getLogger(__name__)


class Publisher:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        exchange: str,
        max_connect_retries: int = 5,
        retry_backoff_seconds: float = 2.0,
    ):
        self._params = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=pika.PlainCredentials(user, password),
        )
        self._exchange = exchange
        self._max_connect_retries = max_connect_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._connection = None
        self._channel = None

    def connect(self) -> None:
        attempt = 0
        while True:
            try:
                self._connection = pika.BlockingConnection(self._params)
                self._channel = self._connection.channel()
                logger.info("connected to rabbitmq at %s:%s", self._params.host, self._params.port)
                return
            except pika.exceptions.AMQPConnectionError:
                attempt += 1
                if attempt > self._max_connect_retries:
                    raise
                wait = self._retry_backoff_seconds * attempt
                logger.warning(
                    "rabbitmq connect failed (attempt %d/%d), retrying in %.1fs",
                    attempt, self._max_connect_retries, wait,
                )
                time.sleep(wait)

    def publish(self, routing_key: str, message: dict) -> None:
        """Publishes one JSON message. Retries exactly once, after
        reconnecting, if the channel/connection had dropped — this is a
        PoC-grade connector, not a guaranteed-delivery pipeline."""
        if self._channel is None:
            self.connect()
        body = json.dumps(message).encode("utf-8")
        properties = pika.BasicProperties(content_type="application/json", delivery_mode=2)
        try:
            self._channel.basic_publish(
                exchange=self._exchange, routing_key=routing_key, body=body, properties=properties,
            )
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError):
            logger.warning("publish to %s failed, reconnecting and retrying once", routing_key)
            self.connect()
            self._channel.basic_publish(
                exchange=self._exchange, routing_key=routing_key, body=body, properties=properties,
            )

    def close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None
