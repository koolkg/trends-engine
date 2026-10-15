"""te-aggregator entrypoint: one cycle at startup, then cycles on the trigger
contract (timer / debounced poll-completed / immediate spike) forever."""
import logging
import signal
import time

import pika

from aggregator.config import load_config
from aggregator.consumer import Consumer, Signals, connection_params
from aggregator.cycle import Aggregator
from aggregator.lock import SingleFlightLock
from aggregator.redis_client import Redis
from aggregator.repository import Repository
from aggregator.trigger import TriggerState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("te-aggregator")


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    c = load_config()
    repo = Repository(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password, c.postgres_db)
    redis = Redis(c.redis_host, c.redis_port)
    agg = Aggregator(repo, redis, SingleFlightLock(redis), c.top_n, c.lookback_hours, c.sparkline_hours,
                     c.decay_per_hour, c.min_z, c.spike_z, c.cache_ttl_seconds)
    # started_at far in the past -> the fallback timer is already due: run once at startup
    trigger = TriggerState(c.fallback_seconds, c.debounce_seconds, c.max_wait_seconds,
                           started_at=time.monotonic() - c.fallback_seconds)
    consumer = Consumer(connection_params(c.rabbitmq_host, c.rabbitmq_port, c.rabbitmq_user, c.rabbitmq_pass),
                        c.poll_queue, c.score_queue, Signals(trigger), agg.run)
    logger.info("timer %.0fs, debounce %.0fs (max %.0fs), top %d, lookback %.0fh", c.fallback_seconds,
                c.debounce_seconds, c.max_wait_seconds, c.top_n, c.lookback_hours)
    try:
        while True:
            try:
                consumer.run()
            except pika.exceptions.AMQPError as exc:
                logger.warning("lost RabbitMQ connection (%s); reconnecting in 5s", exc.__class__.__name__)
                time.sleep(5)
    except KeyboardInterrupt:
        logger.info("shutting down; cycles %s; signals %s", dict(consumer.cycles), dict(consumer.signals.counts))
    finally:
        repo.close()
        redis.close()


if __name__ == "__main__":
    main()
