"""te-detector entrypoint: consume content.normalized forever, reconnecting
to RabbitMQ/Postgres if either goes away. Stops cleanly on SIGTERM.
Window counts and baselines live in memory (see README), so they survive a
broker reconnect but not a process restart."""
import logging
import signal
import time

import pika
import psycopg2

from detector.config import load_config
from detector.consumer import Consumer, connection_params
from detector.detector import Detector
from detector.handler import Handler
from detector.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("te-detector")


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    c = load_config()
    detector = Detector(c.window_minutes, c.ewma_alpha, c.z_threshold, c.min_count, c.warmup_windows, c.ignore_tags)
    store = Store(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password, c.postgres_db)
    handler = Handler(detector, store)
    consumer = Consumer(connection_params(c.rabbitmq_host, c.rabbitmq_port, c.rabbitmq_user, c.rabbitmq_pass),
                        c.queue, c.exchange, c.prefetch, handler, c.idle_flush_seconds)
    logger.info("window %dm, alpha %.2f, spike z>=%.1f, min count %d, warmup %d windows, ignoring %d tags",
                c.window_minutes, c.ewma_alpha, c.z_threshold, c.min_count, c.warmup_windows, len(c.ignore_tags))
    try:
        while True:
            try:
                consumer.run()
            except (pika.exceptions.AMQPError, psycopg2.OperationalError) as exc:
                logger.warning("lost connection (%s); reconnecting in 5s", exc.__class__.__name__)
                store.close()
                time.sleep(5)
    except KeyboardInterrupt:
        logger.info("shutting down; totals %s; %d late posts ignored; %d windows closed",
                    dict(consumer.counts), detector.late_posts, detector.windows_closed)
    finally:
        store.close()


if __name__ == "__main__":
    main()
