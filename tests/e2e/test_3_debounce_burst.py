"""Scenario 3: several source.poll.completed in quick succession ->
exactly one aggregation cycle during the debounce window.

Cycles are observed from outside: each one writes a new trends:v:<version>
key in Redis. The burst is fired right after a cycle completes. A run resets
te-aggregator's 5-minute timer, so nothing else is due in the window, and with
the pipeline drained and te-connector stopped, nothing else sends signals.
"""
import time
import unittest

from e2e.harness import CYCLE_TIMEOUT, current_version, log, poll_completed, redis, replay_fixture_once, wait_for_new_cycle


def versions():
    r = redis()
    try:
        return set(r.keys("trends:v:*"))
    finally:
        r.close()


class DebounceUnderBurst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        replay_fixture_once()

    def test_burst_of_poll_notices_produces_exactly_one_cycle(self):
        # Line up on a fresh cycle so the fallback timer is as far away as possible.
        before = current_version()
        poll_completed()
        aligned = wait_for_new_cycle(before)
        known = versions()

        log("firing a burst of 6 source.poll.completed over ~3 s")
        for _ in range(6):
            poll_completed()
            time.sleep(0.5)
        burst_cycle = wait_for_new_cycle(aligned)
        settle = 30  # longer than the 20 s debounce: a second run would show up in here
        log(f"cycle {burst_cycle} ran; watching {settle}s for a second one")
        time.sleep(settle)

        new = versions() - known
        self.assertEqual(len(new), 1, f"expected exactly one cycle for the burst, saw {sorted(new)}")
        self.assertEqual(current_version(), burst_cycle)

    def test_spike_skips_the_debounce(self):
        """A spike score wakes the aggregator immediately (well under the 20 s debounce)."""
        from e2e.harness import publish
        before = current_version()
        started = time.time()
        publish("trend.score.upserted", [{"term": "#e2e-spike", "windowStart": "2026-09-07T00:00:00Z",
                                          "windowEnd": "2026-09-07T01:00:00Z", "frequency": 0,
                                          "baselineFrequency": 0, "zScore": 0, "velocityLabel": "spike"}])
        wait_for_new_cycle(before, timeout=min(CYCLE_TIMEOUT, 15))
        self.assertLess(time.time() - started, 15)


if __name__ == "__main__":
    unittest.main()
