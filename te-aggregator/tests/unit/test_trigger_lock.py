import unittest

from aggregator.lock import SingleFlightLock
from aggregator.trigger import POLL, SPIKE, TIMER, TriggerState


def run_script(events, end, **kw):
    """events: [(t, "poll"|"spike")]. Steps a 1-second clock to `end` and returns
    [(t, reason)] for every run, calling mark_ran like the consumer does."""
    st = TriggerState(**kw)
    runs, pending = [], sorted(events)
    for t in range(0, end + 1):
        while pending and pending[0][0] <= t:
            _, kind = pending.pop(0)
            (st.on_poll_completed if kind == "poll" else st.on_spike)(t)
        reason = st.due(t)
        if reason:
            st.mark_ran(t)
            runs.append((t, reason))
    return runs


class TestTriggerContract(unittest.TestCase):
    def test_fallback_timer_fires_with_no_signals(self):
        self.assertEqual(run_script([], 650, fallback_s=300), [(300, TIMER), (600, TIMER)])

    def test_single_poll_runs_after_debounce(self):
        self.assertEqual(run_script([(10, "poll")], 100, debounce_s=20), [(30, POLL)])

    def test_burst_of_polls_coalesces_into_one_run(self):
        events = [(10, "poll"), (15, "poll"), (22, "poll"), (30, "poll")]
        self.assertEqual(run_script(events, 100, debounce_s=20), [(50, POLL)])  # 20s after the LAST notice

    def test_steady_trickle_is_capped_by_max_wait(self):
        events = [(t, "poll") for t in range(10, 200, 10)]  # every 10s: debounce never settles
        runs = run_script(events, 200, debounce_s=20, max_wait_s=90)
        self.assertEqual(runs[0], (100, POLL))  # first notice at 10 + 90s cap
        self.assertTrue(all(r == POLL for _, r in runs))

    def test_spike_bypasses_debounce(self):
        self.assertEqual(run_script([(10, "poll"), (12, "spike")], 60, debounce_s=20), [(12, SPIKE)])

    def test_pending_notices_never_queue_multiple_runs(self):
        runs = run_script([(5, "poll")] * 50, 100, debounce_s=20)
        self.assertEqual(runs, [(25, POLL)])

    def test_any_run_resets_the_timer(self):
        runs = run_script([(100, "spike")], 450, fallback_s=300)
        self.assertEqual(runs, [(100, SPIKE), (400, TIMER)])

    def test_seconds_until_due(self):
        st = TriggerState(fallback_s=300, debounce_s=20, max_wait_s=90)
        self.assertEqual(st.seconds_until_due(0), 300)
        st.on_poll_completed(10)
        self.assertEqual(st.seconds_until_due(10), 20)
        st.on_spike(11)
        self.assertEqual(st.seconds_until_due(11), 0)


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None, px=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def eval(self, script, keys=(), args=()):
        if self.data.get(keys[0]) == args[0]:
            del self.data[keys[0]]
            return 1
        return 0


class TestSingleFlightLock(unittest.TestCase):
    def test_only_one_holder(self):
        r = FakeRedis()
        a, b = SingleFlightLock(r), SingleFlightLock(r)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        self.assertTrue(a.release())
        self.assertTrue(b.acquire())

    def test_release_never_deletes_someone_elses_lock(self):
        r = FakeRedis()
        a = SingleFlightLock(r)
        a.acquire()
        r.data[a.key] = "someone-else"  # a's lock expired and another instance took it
        self.assertFalse(a.release())
        self.assertEqual(r.data[a.key], "someone-else")

    def test_context_manager(self):
        r = FakeRedis()
        with SingleFlightLock(r) as got:
            self.assertTrue(got)
        self.assertEqual(r.data, {})


if __name__ == "__main__":
    unittest.main()
