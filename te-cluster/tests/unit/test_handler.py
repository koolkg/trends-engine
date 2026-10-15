import json
import unittest
from datetime import datetime, timedelta, timezone

from cluster.centroids import CentroidIndex
from cluster.handler import SCORE_KEY, Handler, InvalidMessage, parse
from cluster.velocity import VelocityTracker
from tests.fake_embedder import FakeEmbedder

T0 = datetime(2026, 9, 7, tzinfo=timezone.utc)


def msg(nid, text, minutes=0):
    return json.dumps({"normalizedId": nid, "cleanedText": text,
                       "postedAt": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")}).encode()


class FakeStore:
    def __init__(self):
        self.batches, self.persisted, self.fail_next = [], [], False

    def load_centroids(self):
        return list(self.persisted)

    def already_assigned(self, ids):
        done = {nid for batch in self.batches for nid, _, _ in batch[0]}
        return {i for i in ids if i in done}

    def save_batch(self, assignments, touched, evicted, scores):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("postgres down")
        self.batches.append((list(assignments), [c.cluster_id for c in touched], list(evicted), list(scores)))


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, key, message):
        self.published.append((key, message))


def make(threshold=0.5, **velocity):
    velocity.setdefault("warmup_windows", 0)
    velocity.setdefault("min_count", 2)
    return Handler(FakeEmbedder(), CentroidIndex(384, threshold), VelocityTracker(**velocity), FakeStore(),
                   FakePublisher())


class TestParse(unittest.TestCase):
    def test_valid(self):
        p = parse(msg(7, "hello", 5))
        self.assertEqual((p.normalized_id, p.text, p.posted_at), (7, "hello", T0 + timedelta(minutes=5)))

    def test_invalid(self):
        bad = [b"x", b"[]", json.dumps({"normalizedId": "7", "cleanedText": "a", "postedAt": "2026-09-07T00:00:00Z"}),
               json.dumps({"normalizedId": True, "cleanedText": "a", "postedAt": "2026-09-07T00:00:00Z"}),
               json.dumps({"normalizedId": 1, "cleanedText": "  ", "postedAt": "2026-09-07T00:00:00Z"}),
               json.dumps({"normalizedId": 1, "cleanedText": "a", "postedAt": "soon"})]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(InvalidMessage):
                    parse(body if isinstance(body, bytes) else body.encode())


class TestProcessBatch(unittest.TestCase):
    def test_one_embedding_call_per_batch_and_grouping(self):
        h = make()
        r = h.process_batch([
            msg(1, "brands typing in all lowercase now"),
            msg(2, "all lowercase brands typing everywhere"),
            msg(3, "my sourdough starter died again"),
            b"garbage",
        ])
        self.assertEqual(h.embedder.calls, [3])  # one forward pass, invalid message excluded
        self.assertEqual(r.invalid, 1)
        self.assertEqual(r.assignments[1].cluster_id, r.assignments[2].cluster_id)
        self.assertNotEqual(r.assignments[1].cluster_id, r.assignments[3].cluster_id)
        (assignments, touched, evicted, scores), = h.store.batches  # one transaction for the batch
        self.assertEqual(sorted(nid for nid, _, _ in assignments), [1, 2, 3])
        self.assertEqual(len(set(touched)), 2)

    def test_growth_scores_are_persisted_and_published(self):
        h = make()
        h.process_batch([msg(i, f"lowercase brand voice post {i % 2}", minutes=i) for i in range(1, 6)])
        r = h.process_batch([msg(99, "lowercase brand voice post 1", minutes=61)])  # closes hour 0
        self.assertEqual(len(r.scores), 1)
        (key, out), = h.publisher.published
        self.assertEqual(key, SCORE_KEY)
        self.assertEqual(out["frequency"], 5)
        self.assertEqual(out["memberCount"], 6)
        self.assertEqual(set(out), {"clusterId", "windowStart", "windowEnd", "frequency", "baselineFrequency",
                                    "zScore", "velocityLabel", "memberCount"})
        self.assertEqual(h.store.batches[-1][3], r.scores)

    def test_failed_save_resyncs_index_from_db_and_raises(self):
        h = make()
        h.store.fail_next = True
        with self.assertRaises(RuntimeError):
            h.process_batch([msg(1, "anything at all")])
        self.assertEqual(len(h.index), 0)  # memory rolled back to what's persisted (nothing)
        self.assertEqual(h.publisher.published, [])

    def test_redelivered_posts_are_not_clustered_twice(self):
        h = make()
        h.process_batch([msg(1, "brands typing in lowercase"), msg(2, "my cat")])
        members_before = sum(c.member_count for c in h.index.clusters.values())
        r = h.process_batch([msg(1, "brands typing in lowercase"), msg(3, "brands typing lowercase too")])
        self.assertEqual(list(r.assignments), [3])
        self.assertEqual(sum(c.member_count for c in h.index.clusters.values()), members_before + 1)

    def test_all_invalid_batch_touches_nothing(self):
        h = make()
        r = h.process_batch([b"nope"])
        self.assertEqual((r.invalid, h.embedder.calls, h.store.batches), (1, [], []))

    def test_idle_flush_scores_final_window(self):
        h = make()
        h.process_batch([msg(i, "same words here", minutes=i) for i in range(3)])
        self.assertEqual(h.publisher.published, [])
        self.assertEqual(len(h.on_idle()), 1)
        self.assertEqual(h.publisher.published[0][1]["frequency"], 3)


if __name__ == "__main__":
    unittest.main()
