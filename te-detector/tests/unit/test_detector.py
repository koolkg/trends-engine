import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from detector.config import DEFAULT_IGNORE_TAGS, parse_tags
from detector.detector import Detector

T0 = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
STREAM = os.path.join(os.path.dirname(__file__), "..", "fixtures", "normalized_stream.jsonl")


def at(hours, minutes=0):
    return T0 + timedelta(hours=hours, minutes=minutes)


def feed(detector, posts):
    out = []
    for when, text in posts:
        out += detector.add(when, text)
    return out


class TestWindows(unittest.TestCase):
    def test_window_start_floors_to_window(self):
        d = Detector(window_minutes=60)
        self.assertEqual(d.window_start(datetime(2026, 9, 7, 13, 59, 59, tzinfo=timezone.utc)),
                         datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc))
        d15 = Detector(window_minutes=15)
        self.assertEqual(d15.window_start(datetime(2026, 9, 7, 13, 44, tzinfo=timezone.utc)),
                         datetime(2026, 9, 7, 13, 30, tzinfo=timezone.utc))

    def test_naive_timestamps_are_treated_as_utc(self):
        d = Detector()
        self.assertEqual(d.window_start(datetime(2026, 9, 7, 5, 30)), at(5))

    def test_window_closes_only_when_watermark_passes_it(self):
        d = Detector(warmup_windows=0, min_count=1)
        self.assertEqual(feed(d, [(at(0, 5), "#aa"), (at(0, 50), "#aa")]), [])  # same window: nothing closes
        closed = d.add(at(1, 1), "#bb")
        self.assertEqual([(s.term, s.frequency, s.window_start) for s in closed], [("#aa", 2, at(0))])

    def test_gap_windows_are_closed_and_decay_baselines(self):
        d = Detector(warmup_windows=0, min_count=1)
        feed(d, [(at(0), "#aa"), (at(0, 1), "#aa")])
        d.add(at(5), "#cc")  # closes hours 0-4: one with #aa, four empty
        self.assertEqual(d.windows_closed, 5)
        self.assertLess(d.baselines["#aa"].mean, 0.5)

    def test_late_posts_are_counted_and_ignored(self):
        d = Detector(warmup_windows=0, min_count=1)
        feed(d, [(at(0), "#aa"), (at(2), "#aa")])
        self.assertEqual(d.add(at(1, 30), "#aa"), [])  # hour 1 already closed
        self.assertEqual(d.late_posts, 1)

    def test_flush_closes_the_open_window(self):
        d = Detector(warmup_windows=0, min_count=1)
        feed(d, [(at(3), "#aa"), (at(3, 10), "#aa")])
        flushed = d.flush()
        self.assertEqual([(s.term, s.frequency) for s in flushed], [("#aa", 2)])
        self.assertEqual(Detector().flush(), [])  # nothing seen yet: no-op


class TestScoringBehaviour(unittest.TestCase):
    def burst_stream(self):
        posts = []
        for h in range(6):  # steady background: #steady twice an hour
            posts += [(at(h, 5), "#steady"), (at(h, 35), "#steady")]
        posts += [(at(6, m), "boom #burst #steady") for m in range(0, 50, 5)]  # 10x #burst in hour 6
        posts += [(at(7, 1), "#steady")]  # moves the watermark past hour 6
        return posts

    def test_burst_spikes_and_steady_term_does_not(self):
        d = Detector(warmup_windows=2, min_count=2)
        scores = feed(d, self.burst_stream())
        spikes = {s.term for s in scores if s.velocity_label == "spike"}
        self.assertIn("#burst", spikes)
        self.assertTrue(all(s.velocity_label != "spike" for s in scores if s.window_start < at(6)))

    def test_warmup_windows_emit_nothing(self):
        d = Detector(warmup_windows=3, min_count=1)
        scores = feed(d, [(at(h), "#aa") for h in range(4)])
        self.assertEqual(scores, [])  # windows 0-2 closed during warmup
        self.assertEqual([s.window_start for s in d.add(at(4), "#aa")], [at(3)])

    def test_min_count_filters_output_but_still_updates_baseline(self):
        d = Detector(warmup_windows=0, min_count=5)
        scores = feed(d, [(at(0), "#aa"), (at(1), "#aa")])
        self.assertEqual(scores, [])
        self.assertGreater(d.baselines["#aa"].mean, 0)

    def test_baseline_reported_is_the_pre_window_mean(self):
        d = Detector(warmup_windows=0, min_count=1, alpha=0.5)
        feed(d, [(at(0), "#aa"), (at(0, 1), "#aa"), (at(1), "#aa")])  # closes hour 0 (n=2): baseline before = 0
        s, = d.add(at(2), "#zz")  # closes hour 1 (n=1): baseline before = 1.0
        self.assertEqual((s.frequency, s.baseline_frequency), (1, 1.0))

    def test_dead_terms_are_forgotten(self):
        d = Detector(warmup_windows=0, min_count=1)
        d.add(at(0), "#once")
        d.add(at(60), "#later")
        self.assertNotIn("#once", d.baselines)


class TestFixtureStream(unittest.TestCase):
    """The DET IT, done offline: te-normalizer's output for te-connector's
    fixture (tests/fixtures/normalized_stream.jsonl, 1,207 posts) through the
    detector with default settings."""

    TREND_TAGS = {"#lowercase", "#brandvoice", "#productdemo", "#pov", "#unboxing", "#dupe", "#dupes",
                  "#dupealert", "#budgetbeauty", "#affordablemakeup", "#emailmarketing", "#subjectlines",
                  "#emailmarketingtips", "#quietluxury", "#oldmoney", "#minimalist", "#founderstory"}

    @classmethod
    def setUpClass(cls):
        d = Detector(ignore_tags=parse_tags(DEFAULT_IGNORE_TAGS))
        with open(STREAM, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        cls.scores = []
        for r in rows:
            cls.scores += d.add(datetime.fromisoformat(r["postedAt"].replace("Z", "+00:00")), r["cleanedText"])
        while d.open_counts:
            cls.scores += d.flush()
        cls.spikes = [s for s in cls.scores if s.velocity_label == "spike"]

    def test_lowercase_spike_is_detected_in_the_evening(self):
        hours = {s.window_start.hour for s in self.spikes if s.term == "#lowercase"}
        self.assertTrue(hours & {18, 19, 20}, hours)

    def test_every_spike_is_a_real_trend_hashtag(self):
        self.assertTrue(self.spikes)
        self.assertEqual({s.term for s in self.spikes} - self.TREND_TAGS, set())

    def test_output_volume_is_small(self):
        self.assertLess(len(self.scores), 200)  # vs ~20k (term, window) pairs if every term were emitted


if __name__ == "__main__":
    unittest.main()
