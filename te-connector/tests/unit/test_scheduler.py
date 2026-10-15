import threading
import time
import unittest

from connector.scheduler import PollScheduler


class TestPollScheduler(unittest.TestCase):
    def test_runs_on_a_fixed_rate_until_max_runs(self):
        calls = []
        s = PollScheduler(lambda: calls.append(time.monotonic()) or True, 0.05, max_runs=4).start()
        s.join(timeout=2)
        self.assertFalse(s.alive)
        self.assertEqual(s.runs, 4)
        self.assertEqual(len(calls), 4)
        self.assertGreaterEqual(calls[-1] - calls[0], 0.14)   # 3 gaps of ~0.05 s

    def test_first_poll_runs_immediately(self):
        started = time.monotonic()
        seen = []
        s = PollScheduler(lambda: seen.append(time.monotonic()) or True, 10, max_runs=1).start()
        s.join(timeout=2)
        self.assertLess(seen[0] - started, 0.5)

    def test_stop_ends_a_long_wait_promptly(self):
        s = PollScheduler(lambda: True, 30).start()
        time.sleep(0.1)                 # first poll done, now waiting 30 s
        t0 = time.monotonic()
        s.stop()
        s.join(timeout=2)
        self.assertFalse(s.alive)
        self.assertLess(time.monotonic() - t0, 1)
        self.assertEqual(s.runs, 1)

    def test_job_returning_false_stops_without_counting_a_run(self):
        results = iter([True, True, False])
        s = PollScheduler(lambda: next(results), 0).start()
        s.join(timeout=2)
        self.assertEqual(s.runs, 2)

    def test_an_overrunning_poll_does_not_cause_a_burst(self):
        calls = []

        def slow():
            calls.append(time.monotonic())
            time.sleep(0.12)            # longer than the 0.05 s interval
            return True

        s = PollScheduler(slow, 0.05, max_runs=3).start()
        s.join(timeout=3)
        gaps = [b - a for a, b in zip(calls, calls[1:])]
        for g in gaps:
            self.assertGreaterEqual(g, 0.11)   # each starts after the previous finished, never stacked

    def test_job_error_is_recorded_and_stops_the_thread(self):
        def boom():
            raise RuntimeError("broker gone")

        s = PollScheduler(boom, 0).start()
        s.join(timeout=2)
        self.assertFalse(s.alive)
        self.assertIsInstance(s.error, RuntimeError)

    def test_rejects_negative_interval(self):
        with self.assertRaises(ValueError):
            PollScheduler(lambda: True, -1)


if __name__ == "__main__":
    unittest.main()
