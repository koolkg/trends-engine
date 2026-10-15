import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from cluster.centroids import CentroidIndex, Cluster

T0 = datetime(2026, 9, 7, tzinfo=timezone.utc)


def unit(*xs):
    v = np.array(xs + (0.0,) * (4 - len(xs)), dtype=np.float32)
    return v / np.linalg.norm(v)


class TestJoinOrSeed(unittest.TestCase):
    def test_first_post_seeds(self):
        idx = CentroidIndex(dim=4, threshold=0.7)
        a = idx.assign(unit(1, 0), T0)
        self.assertTrue(a.seeded)
        self.assertEqual((len(idx), a.similarity), (1, 1.0))

    def test_similar_joins_and_dissimilar_seeds(self):
        idx = CentroidIndex(dim=4, threshold=0.7)
        first = idx.assign(unit(1, 0), T0)
        near = idx.assign(unit(1, 0.3), T0)    # cos ~0.958 -> join
        far = idx.assign(unit(0, 1), T0)       # cos 0 -> seed
        self.assertEqual(near.cluster_id, first.cluster_id)
        self.assertFalse(near.seeded)
        self.assertTrue(far.seeded)
        self.assertEqual(len(idx), 2)

    def test_threshold_boundary(self):
        # cos(a, b) = 0.6 exactly: joins at threshold 0.6, seeds at 0.6001
        a, b = unit(1, 0), np.array([0.6, 0.8, 0, 0], dtype=np.float32)
        for threshold, joins in ((0.6, True), (0.6001, False)):
            with self.subTest(threshold=threshold):
                idx = CentroidIndex(dim=4, threshold=threshold)
                idx.assign(a, T0)
                self.assertEqual(not idx.assign(b, T0).seeded, joins)

    def test_running_mean_update_matches_hand_computed(self):
        idx = CentroidIndex(dim=4, threshold=0.0)
        cid = idx.assign(unit(1, 0), T0).cluster_id
        idx.assign(unit(0, 1), T0)  # joins (threshold 0): c' = normalize(c + (v - c)/2) = normalize([.5,.5])
        np.testing.assert_allclose(idx.clusters[cid].centroid, unit(1, 1), atol=1e-6)
        self.assertEqual(idx.clusters[cid].member_count, 2)
        self.assertAlmostEqual(float(np.linalg.norm(idx.clusters[cid].centroid)), 1.0, places=6)
        # lookup matrix is kept in sync with the updated centroid
        self.assertAlmostEqual(idx.nearest(unit(1, 1))[1], 1.0, places=5)

    def test_unnormalized_input_is_normalized(self):
        idx = CentroidIndex(dim=4, threshold=0.9)
        a = idx.assign(np.array([3, 0, 0, 0], dtype=np.float32), T0)
        b = idx.assign(np.array([10, 0, 0, 0], dtype=np.float32), T0)
        self.assertEqual(a.cluster_id, b.cluster_id)
        self.assertAlmostEqual(b.similarity, 1.0, places=3)


class TestEvictionAndLoad(unittest.TestCase):
    def test_cold_clusters_are_evicted_by_post_time(self):
        idx = CentroidIndex(dim=4, threshold=0.7, evict_after=timedelta(hours=24))
        old = idx.assign(unit(1, 0), T0).cluster_id
        fresh = idx.assign(unit(0, 1), T0 + timedelta(hours=20)).cluster_id
        self.assertEqual(idx.evict_stale(T0 + timedelta(hours=25)), [old])
        self.assertEqual(list(idx.clusters), [fresh])
        self.assertTrue(idx.assign(unit(1, 0), T0 + timedelta(hours=25)).seeded)  # evicted one is really gone

    def test_joining_refreshes_last_assigned(self):
        idx = CentroidIndex(dim=4, threshold=0.7, evict_after=timedelta(hours=24))
        cid = idx.assign(unit(1, 0), T0).cluster_id
        idx.assign(unit(1, 0.1), T0 + timedelta(hours=23))
        self.assertEqual(idx.evict_stale(T0 + timedelta(hours=30)), [])
        self.assertIn(cid, idx.clusters)

    def test_load_rebuilds_lookup(self):
        idx = CentroidIndex(dim=4, threshold=0.7)
        idx.load([Cluster("a", unit(1, 0), 5, T0), Cluster("b", unit(0, 1), 2, T0)])
        self.assertEqual(idx.nearest(unit(0.1, 1)), ("b", idx.nearest(unit(0.1, 1))[1]))
        self.assertEqual(len(idx), 2)
        idx.load([])
        self.assertEqual(idx.nearest(unit(1, 0)), (None, -1.0))


if __name__ == "__main__":
    unittest.main()
