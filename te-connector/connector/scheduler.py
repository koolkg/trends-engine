"""CONN-6: runs the poll job on a schedule, in its own thread.

Fixed-rate, not fixed-delay: polls start every `interval_seconds` measured
from the first one, so a slow poll doesn't push every later poll back. If a
poll overruns its slot, the next one starts straight away; missed slots are
not replayed in a burst.

One scheduler thread on purpose: the batches come from one ordered fixture
(posts in postedAt order, which te-detector's hourly windows rely on), and
pika's BlockingConnection isn't thread-safe. A second polling thread would
reorder posts and share a connection.

The job returns False when there's nothing left to poll (a non-looping
fixture ran out); the scheduler then stops on its own.
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class PollScheduler:
    def __init__(self, job: Callable[[], bool], interval_seconds: float, max_runs: Optional[int] = None,
                 clock: Callable[[], float] = time.monotonic, name: str = "poll-scheduler"):
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        self._job = job
        self._interval = interval_seconds
        self._max_runs = max_runs
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self.runs = 0
        self.last_run_at: Optional[datetime] = None   # wall-clock time of the last finished poll
        self.error: Optional[BaseException] = None    # set if the job raised; the scheduler then stops

    def start(self) -> "PollScheduler":
        self._thread.start()
        return self

    def stop(self) -> None:
        """Asks the thread to stop. A poll already running finishes first."""
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        next_at = self._clock()
        try:
            while not self._stop.is_set():
                if self._job() is False:
                    logger.info("nothing left to poll; scheduler stopping after %d polls", self.runs)
                    return
                self.runs += 1
                self.last_run_at = datetime.now(timezone.utc)
                if self._max_runs is not None and self.runs >= self._max_runs:
                    return
                next_at += self._interval
                now = self._clock()
                if next_at < now:      # the poll overran its slot: start the next one now, no burst
                    next_at = now
                self._stop.wait(next_at - now)
        except Exception as exc:  # surfaced by run(), which re-raises it in the main thread
            self.error = exc
            logger.exception("poll failed; scheduler stopping")
