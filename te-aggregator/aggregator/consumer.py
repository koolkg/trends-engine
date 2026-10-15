"""AGG-1 wiring: listens on both of te-aggregator's queues and runs a cycle
whenever the trigger state machine says one is owed.

- q.aggregator.source-poll-completed -> on_poll_completed (debounced)
- q.aggregator.trend-score-upserted  -> on_spike when velocityLabel == "spike"
  (te-detector's trend.score.upserted and te-cluster's cluster.score.upserted
  are both bound to it)

Messages are pure signals: they're acked on receipt and collapse into the
trigger's state (one "run owed" flag), never a queue of runs.
"""
import json
import logging
import time
from collections import Counter
from typing import Callable, Optional

import pika

from aggregator.trigger import TriggerState

logger = logging.getLogger(__name__)


def connection_params(host, port, user, password) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(host=host, port=port, credentials=pika.PlainCredentials(user, password),
                                     heartbeat=60)


class Signals:
    """Turns raw messages into trigger events. Separate from pika for testing."""

    def __init__(self, trigger: TriggerState, clock: Callable[[], float] = time.monotonic):
        self.trigger, self.clock = trigger, clock
        self.counts: Counter = Counter()

    def on_message(self, routing_key: str, body: bytes) -> None:
        now = self.clock()
        if routing_key == "source.poll.completed":
            self.trigger.on_poll_completed(now)
            self.counts["polls"] += 1
            return
        try:
            msg = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.counts["invalid"] += 1
            return
        if isinstance(msg, dict) and msg.get("velocityLabel") == "spike":
            self.trigger.on_spike(now)
            self.counts["spikes"] += 1
            logger.info("spike from %s (%s): waking aggregation", routing_key, msg.get("term") or msg.get("clusterId"))
        else:
            self.counts["scores"] += 1


class Consumer:
    def __init__(self, params, poll_queue: str, score_queue: str, signals: Signals, run_cycle: Callable[[str], object],
                 clock: Callable[[], float] = time.monotonic):
        self.params, self.poll_queue, self.score_queue = params, poll_queue, score_queue
        self.signals, self.run_cycle, self.clock = signals, run_cycle, clock
        self.cycles: Counter = Counter()

    def run(self, max_cycles: Optional[int] = None, max_seconds: Optional[float] = None) -> Counter:
        connection = pika.BlockingConnection(self.params)
        started = self.clock()
        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=200)

            def on_msg(ch, method, _props, body):
                self.signals.on_message(method.routing_key, body)
                ch.basic_ack(method.delivery_tag)

            channel.basic_consume(self.poll_queue, on_msg)
            channel.basic_consume(self.score_queue, on_msg)
            logger.info("listening on %s and %s", self.poll_queue, self.score_queue)
            trigger = self.signals.trigger
            while True:
                wait = min(1.0, trigger.seconds_until_due(self.clock()))
                connection.process_data_events(time_limit=wait)
                reason = trigger.due(self.clock())
                if reason:
                    trigger.mark_ran(self.clock())  # everything owed so far is covered by this run
                    try:
                        self.run_cycle(reason)
                        self.cycles[reason] += 1
                    except Exception:
                        logger.exception("aggregation cycle (%s) failed; next attempt on the next trigger", reason)
                        self.cycles["failed"] += 1
                if max_cycles is not None and sum(self.cycles.values()) >= max_cycles:
                    break
                if max_seconds is not None and self.clock() - started >= max_seconds:
                    break
        finally:
            if connection.is_open:
                connection.close()
        return self.cycles
