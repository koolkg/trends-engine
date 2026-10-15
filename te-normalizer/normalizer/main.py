"""te-normalizer entrypoint: consume content.raw.ingested forever,
reconnecting to RabbitMQ/Postgres if either goes away. Stops cleanly on
SIGTERM (docker stop) or Ctrl-C."""
import logging
import signal
import time

import pika
import psycopg2

from normalizer.config import load_config
from normalizer.consumer import Consumer, connection_params
from normalizer.pipeline import process
from normalizer import relevance as relevance_mod
from normalizer.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("te-normalizer")


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    config = load_config()
    relevance = relevance_mod.build(config)
    store = Store(config.postgres_host, config.postgres_port, config.postgres_user,
                  config.postgres_password, config.postgres_db)
    consumer = Consumer(
        connection_params(config.rabbitmq_host, config.rabbitmq_port, config.rabbitmq_user, config.rabbitmq_pass),
        queue=config.queue,
        exchange=config.exchange,
        prefetch=config.prefetch,
        handler=lambda body, publisher: process(body, store, publisher, config.relevance_threshold, relevance),
    )
    try:
        while True:
            try:
                consumer.run()
            except (pika.exceptions.AMQPError, psycopg2.OperationalError) as exc:
                logger.warning("lost connection (%s); reconnecting in 5s", exc.__class__.__name__)
                store.close()
                time.sleep(5)
    except KeyboardInterrupt:
        logger.info("shutting down; totals: %s; relevance decided by: %s; disagreements: %s", dict(consumer.counts),
                    dict(relevance.counts), dict(getattr(relevance, "disagreements", {})))
    finally:
        store.close()


if __name__ == "__main__":
    main()
