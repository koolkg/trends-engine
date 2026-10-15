"""te-cluster entrypoint: load the model and the persisted centroids, then
consume content.normalized forever, reconnecting if RabbitMQ or Postgres goes
away. Stops cleanly on SIGTERM."""
import logging
import signal
import time
from datetime import timedelta

import pika
import psycopg2

from cluster.centroids import CentroidIndex
from cluster.config import load_config
from cluster.consumer import Consumer, connection_params
from cluster.embedder import MiniLMEmbedder
from cluster.handler import Handler
from cluster.store import Store
from cluster.velocity import VelocityTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("te-cluster")


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    c = load_config()
    embedder = MiniLMEmbedder(c.model_name)
    index = CentroidIndex(embedder.dim, c.similarity_threshold, timedelta(hours=c.evict_after_hours))
    velocity = VelocityTracker(c.window_minutes, c.ewma_alpha, c.z_threshold, c.min_count, c.warmup_windows)
    store = Store(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password, c.postgres_db)
    handler = Handler(embedder, index, velocity, store)
    consumer = Consumer(connection_params(c.rabbitmq_host, c.rabbitmq_port, c.rabbitmq_user, c.rabbitmq_pass),
                        c.queue, c.exchange, handler, c.batch_size, c.batch_wait_ms, c.idle_flush_seconds)
    logger.info("join threshold %.2f, evict after %.0fh, window %dm, spike z>=%.1f", c.similarity_threshold,
                c.evict_after_hours, c.window_minutes, c.z_threshold)
    try:
        while True:
            try:
                handler.load()
                consumer.run()
            except (pika.exceptions.AMQPError, psycopg2.OperationalError) as exc:
                logger.warning("lost connection (%s); reconnecting in 5s", exc.__class__.__name__)
                store.close()
                time.sleep(5)
    except KeyboardInterrupt:
        logger.info("shutting down; totals %s; %d active clusters", dict(consumer.counts), len(index))
    finally:
        store.close()


if __name__ == "__main__":
    main()
