"""CONN-5: the poll cycle. Publishes one batch to content.raw.ingested,
then exactly one source.poll.completed carrying that batch's pollCycleId
and itemCount, matching the event schemas.

CONN-6: the cycle runs on a schedule in its own thread (connector.scheduler),
every CONNECTOR_POLL_INTERVAL_SECONDS. The main thread only waits, and on
SIGTERM (docker stop) asks the scheduler to stop after the poll in progress.

Every published message is logged in full, one JSON line each, so `make logs
poller` shows exactly what went onto the bus (CONNECTOR_LOG_MESSAGES=false
turns it off and keeps only the one-line cycle summary).
"""
import json
import logging
import signal
import uuid
from datetime import datetime, timezone
from typing import Optional

from connector.batch_reader import iter_batches, load_fixture
from connector.config import Config, load_config
from connector.publisher import Publisher
from connector.scheduler import PollScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("te-connector")

RAW_INGESTED_KEY = "content.raw.ingested"
POLL_COMPLETED_KEY = "source.poll.completed"


def make_poll_cycle_id(now: datetime) -> str:
    # e.g. pc_20260910_1404_a1b2c3 — doc's example format plus a short
    # random suffix so cycles landing in the same second-truncated minute
    # (poll_interval can be < 60s) never collide.
    return f"pc_{now.strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:6]}"


def run(config: Optional[Config] = None, publisher: Optional[Publisher] = None, max_cycles: Optional[int] = None) -> int:
    """Runs the poll cycle on a schedule. With max_cycles=None (the default)
    this runs until stopped (SIGTERM / docker stop) or, with looping off,
    until the fixture runs out. Tests and one-shot invocations pass a finite
    max_cycles instead. Returns the number of cycles actually run.
    """
    config = config or load_config()
    items = load_fixture(config.fixture_path)
    batches = iter_batches(items, config.batch_size, loop=config.loop_fixture)

    owns_publisher = publisher is None
    publisher = publisher or Publisher(
        host=config.rabbitmq_host,
        port=config.rabbitmq_port,
        user=config.rabbitmq_user,
        password=config.rabbitmq_pass,
        exchange=config.exchange,
    )

    def publish(routing_key: str, message: dict) -> None:
        publisher.publish(routing_key, message)
        if config.log_messages:
            logger.info("published %s %s", routing_key, json.dumps(message, ensure_ascii=False))

    def poll_once() -> bool:
        """One poll cycle: unchanged from the original loop body. False = fixture exhausted."""
        batch = next(batches, None)
        if batch is None:
            return False
        now = datetime.now(timezone.utc)
        for item in batch:
            message = dict(item)
            message.setdefault("sourceId", config.source_id)
            publish(RAW_INGESTED_KEY, message)

        poll_cycle_id = make_poll_cycle_id(now)
        publish(
            POLL_COMPLETED_KEY,
            {
                "sourceId": config.source_id,
                "pollCycleId": poll_cycle_id,
                "itemCount": len(batch),
                "completedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        logger.info("poll cycle %s: published %d items to %s", poll_cycle_id, len(batch), RAW_INGESTED_KEY)
        return True

    scheduler = PollScheduler(poll_once, config.poll_interval_seconds, max_runs=max_cycles)
    logger.info("polling every %.0fs, %d posts per poll", config.poll_interval_seconds, config.batch_size)
    try:
        scheduler.start()
        while scheduler.alive:          # short joins keep the main thread responsive to SIGTERM
            scheduler.join(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("stopping: finishing the poll in progress")
        scheduler.stop()
        scheduler.join()
    finally:
        if owns_publisher:
            publisher.close()
    if scheduler.error is not None:
        raise scheduler.error           # a failed poll still ends the process, so Docker restarts it
    return scheduler.runs


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    run()
