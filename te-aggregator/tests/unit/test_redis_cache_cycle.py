import json
import unittest
from datetime import datetime, timedelta, timezone

from aggregator import cache
from aggregator.consumer import Signals
from aggregator.cycle import Aggregator
from aggregator.ranking import Post
from aggregator.redis_client import Redis, RedisError
from aggregator.trigger import TriggerState

NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)


class FakeSocket:
    def __init__(self, replies: bytes, chunk: int = 65536):
        self.sent, self._replies, self._chunk = b"", replies, chunk

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        n = min(n, self._chunk)
        chunk, self._replies = self._replies[:n], self._replies[n:]
        return chunk

    def close(self):
        pass


def client(replies: bytes, chunk: int = 65536) -> Redis:
    r = Redis()
    r._sock = FakeSocket(replies, chunk)
    return r


class TestRESP(unittest.TestCase):
    def test_encodes_commands_as_bulk_string_arrays(self):
        r = client(b"+OK\r\n")
        self.assertTrue(r.set("k", "héllo", ex=5, nx=True))
        self.assertEqual(r._sock.sent, b"*6\r\n$3\r\nSET\r\n$1\r\nk\r\n$6\r\nh\xc3\xa9llo\r\n$2\r\nEX\r\n$1\r\n5\r\n$2\r\nNX\r\n")

    def test_parses_every_reply_type(self):
        r = client(b"$5\r\nhello\r\n$-1\r\n:3\r\n*2\r\n$1\r\na\r\n:1\r\n-ERR boom\r\n")
        self.assertEqual(r.get("a"), b"hello")
        self.assertIsNone(r.get("missing"))
        self.assertEqual(r.delete("x", "y", "z"), 3)
        self.assertEqual(r.execute("WHATEVER"), [b"a", 1])
        with self.assertRaises(RedisError):
            r.execute("BAD")

    def test_set_nx_miss_returns_false(self):
        self.assertFalse(client(b"$-1\r\n").set("k", "v", nx=True))

    def test_reply_split_across_recv_calls(self):
        r = client(b"$11\r\nhello world\r\n:7\r\n", chunk=3)  # the network hands us 3 bytes at a time
        self.assertEqual(r.get("k"), b"hello world")
        self.assertEqual(r.execute("INCR", "n"), 7)


class DictRedis:
    def __init__(self):
        self.data, self.ttl = {}, {}

    def set(self, key, value, ex=None, px=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    def eval(self, script, keys=(), args=()):
        if self.data.get(keys[0]) == args[0]:
            del self.data[keys[0]]
            return 1
        return 0


class TestCache(unittest.TestCase):
    def test_versioned_key_then_pointer_swap(self):
        r = DictRedis()
        v = cache.push(r, [{"trendId": "t1"}], NOW, ttl_seconds=600)
        self.assertEqual(r.data[cache.CURRENT_KEY], v)
        payload = json.loads(r.data[cache.VERSION_KEY.format(v)])
        self.assertEqual((payload["version"], payload["computedAt"], payload["trends"]),
                         (v, "2026-09-07T20:00:00Z", [{"trendId": "t1"}]))
        self.assertEqual(r.ttl[cache.VERSION_KEY.format(v)], 600)
        self.assertNotIn(cache.CURRENT_KEY, r.ttl)  # pointer itself never expires


class FakeRepo:
    def __init__(self, now=NOW):
        self.now, self.written = now, None

    def data_now(self):
        return self.now

    def term_rows(self, since):
        return [("#lowercase", NOW - timedelta(hours=1), NOW, 10, 1.0, 6.0),
                ("#steady", NOW - timedelta(hours=1), NOW, 50, 49.0, 0.1)]

    def cluster_rows(self, since):
        return []

    def posts_for_term(self, tag, since, until):
        return [Post(i, f"brands in lowercase {tag}", "social", NOW - timedelta(minutes=10 * i), "@a", "x", i)
                for i in range(1, 4)]

    def posts_for_cluster(self, cid, since, until):
        return []

    def write_ranking(self, trends, computed_at):
        self.written = (trends, computed_at)


class FakeLock:
    def __init__(self, free=True):
        self.free, self.released = free, False

    def acquire(self):
        return self.free

    def release(self):
        self.released = True


class TestCycle(unittest.TestCase):
    def test_cycle_writes_postgres_then_redis(self):
        repo, r, lock = FakeRepo(), DictRedis(), FakeLock()
        trends = Aggregator(repo, r, lock, clock=lambda: NOW).run("test")
        self.assertEqual([t["name"] for t in trends], ["#lowercase"])  # #steady never rose
        self.assertEqual(repo.written[0], trends)
        current = r.data[cache.CURRENT_KEY]
        self.assertEqual(json.loads(r.data[cache.VERSION_KEY.format(current)])["trends"], trends)
        self.assertTrue(lock.released)

    def test_locked_elsewhere_skips(self):
        repo = FakeRepo()
        self.assertIsNone(Aggregator(repo, DictRedis(), FakeLock(free=False)).run("test"))
        self.assertIsNone(repo.written)

    def test_no_data_yet_still_publishes_an_empty_ranking(self):
        repo, r = FakeRepo(now=None), DictRedis()
        self.assertEqual(Aggregator(repo, r, FakeLock(), clock=lambda: NOW).run("test"), [])
        self.assertEqual(repo.written, ([], NOW))
        current = r.data[cache.CURRENT_KEY]
        self.assertEqual(json.loads(r.data[cache.VERSION_KEY.format(current)])["trends"], [])

    def test_lock_released_even_if_cycle_fails(self):
        class Boom(FakeRepo):
            def term_rows(self, since):
                raise RuntimeError("db down")
        lock = FakeLock()
        with self.assertRaises(RuntimeError):
            Aggregator(Boom(), DictRedis(), lock).run("test")
        self.assertTrue(lock.released)


class TestSignals(unittest.TestCase):
    def test_routing(self):
        st = TriggerState(debounce_s=20)
        sig = Signals(st, clock=lambda: 100.0)
        sig.on_message("source.poll.completed", b"{}")
        self.assertEqual(st.pending_since, 100.0)
        sig.on_message("trend.score.upserted", json.dumps({"term": "#a", "velocityLabel": "rising"}).encode())
        self.assertFalse(st.spike_pending)
        sig.on_message("cluster.score.upserted", json.dumps({"clusterId": "c", "velocityLabel": "spike"}).encode())
        self.assertTrue(st.spike_pending)
        sig.on_message("trend.score.upserted", b"not json")
        self.assertEqual(dict(sig.counts), {"polls": 1, "scores": 1, "spikes": 1, "invalid": 1})


if __name__ == "__main__":
    unittest.main()
