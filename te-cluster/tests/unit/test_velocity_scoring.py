import math
import unittest
from datetime import datetime, timedelta, timezone

from cluster.scoring import SPIKE, Baseline, velocity_label
from cluster.velocity import VelocityTracker

T0 = datetime(2026, 9, 7, tzinfo=timezone.utc)


def at(h, m=0):
    return T0 + timedelta(hours=h, minutes=m)


class TestScoring(unittest.TestCase):
    def test_same_math_as_detector(self):
        b = Baseline(alpha=0.5)
        b.update(4)
        b.update(0)
        self.assertAlmostEqual((b.mean, b.var), (1.0, 3.0))
        self.assertAlmostEqual(b.z(5), (5 - 1) / math.sqrt(3 + 1 + 1))
        self.assertEqual(velocity_label(3.0, 3.0), SPIKE)


class TestVelocityTracker(unittest.TestCase):
    def test_growing_cluster_spikes_and_singletons_never_surface(self):
        v = VelocityTracker(window_minutes=60, warmup_windows=1, min_count=3)
        out = []
        for h in range(4):
            out += v.add(at(h, 1), "steady")
            out += v.add(at(h, 30), f"singleton-{h}")
        out += [s for m in range(0, 50, 5) for s in v.add(at(4, m), "hot")]
        out += v.add(at(5, 1), "steady")
        self.assertEqual({s.cluster_id for s in out}, {"hot"})  # singletons/steady stay under min_count
        self.assertEqual(out[0].velocity_label, SPIKE)
        self.assertEqual((out[0].frequency, out[0].window_start), (10, at(4)))

    def test_late_and_flush_and_forget(self):
        v = VelocityTracker(warmup_windows=0, min_count=1)
        v.add(at(0), "a")
        v.add(at(2), "a")
        self.assertEqual(v.add(at(1), "a"), [])
        self.assertEqual(v.late_posts, 1)
        self.assertEqual([s.cluster_id for s in v.flush()], ["a"])
        v.forget(["a"])
        self.assertNotIn("a", v.baselines)


if __name__ == "__main__":
    unittest.main()
