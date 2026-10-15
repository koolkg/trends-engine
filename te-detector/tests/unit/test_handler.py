import json
import unittest
from datetime import datetime, timedelta, timezone

from detector.detector import Detector
from detector.handler import SCORE_KEY, Handler, InvalidMessage, parse

T0 = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def msg(minutes, text):
    return json.dumps({"normalizedId": 1, "cleanedText": text,
                       "postedAt": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")}).encode()


class FakeStore:
    def __init__(self, fail_times=0):
        self.saved, self.fail_times = [], fail_times

    def save(self, scores):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("postgres down")
        self.saved.extend(scores)


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, routing_key, message):
        self.published.append((routing_key, message))


class TestParse(unittest.TestCase):
    def test_valid(self):
        when, text = parse(msg(5, "#aa"))
        self.assertEqual((when, text), (T0 + timedelta(minutes=5), "#aa"))

    def test_invalid(self):
        for body in (b"nope", b"[]", json.dumps({"cleanedText": "x"}).encode(),
                     json.dumps({"cleanedText": "x", "postedAt": "yesterday"}).encode()):
            with self.subTest(body=body):
                with self.assertRaises(InvalidMessage):
                    parse(body)


class TestHandler(unittest.TestCase):
    def make(self, store=None):
        h = Handler(Detector(warmup_windows=0, min_count=2), store or FakeStore(), FakePublisher())
        return h

    def test_closed_window_is_stored_then_published_in_doc_schema(self):
        h = self.make()
        for m in (1, 2, 3):
            h.on_message(msg(m, "#aa"))
        h.on_message(msg(61, "#bb"))
        self.assertEqual([s.term for s in h.store.saved], ["#aa"])
        (key, out), = h.publisher.published
        self.assertEqual(key, SCORE_KEY)
        self.assertEqual(set(out), {"term", "windowStart", "windowEnd", "frequency", "baselineFrequency",
                                    "zScore", "velocityLabel"})
        self.assertEqual((out["term"], out["frequency"], out["windowStart"], out["windowEnd"]),
                         ("#aa", 3, "2026-09-07T00:00:00Z", "2026-09-07T01:00:00Z"))

    def test_idle_flush_emits_last_window(self):
        h = self.make()
        h.on_message(msg(1, "#aa"))
        h.on_message(msg(2, "#aa"))
        self.assertEqual(h.publisher.published, [])
        h.on_idle()
        self.assertEqual([m["term"] for _, m in h.publisher.published], ["#aa"])

    def test_scores_survive_a_failed_save_and_go_out_next_time(self):
        h = self.make(FakeStore(fail_times=1))
        h.on_message(msg(1, "#aa"))
        h.on_message(msg(2, "#aa"))
        with self.assertRaises(RuntimeError):
            h.on_message(msg(61, "#bb"))  # closes hour 0, save fails
        self.assertEqual(len(h.pending), 1)
        h.on_message(msg(62, "#bb"))      # nothing new closes, but pending is retried
        self.assertEqual([s.term for s in h.store.saved], ["#aa"])
        self.assertEqual(h.pending, [])
        self.assertEqual(len(h.publisher.published), 1)


if __name__ == "__main__":
    unittest.main()
