import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone

from connector.config import Config
from connector.main import make_poll_cycle_id, run


class FakePublisher:
    """Records every publish() call in-memory instead of talking to a real
    broker — this is what CONN-5's poll-cycle loop is tested against."""

    def __init__(self):
        self.published = []
        self.closed = False

    def publish(self, routing_key, message):
        self.published.append((routing_key, message))

    def close(self):
        self.closed = True


class TestMakePollCycleId(unittest.TestCase):
    def test_format(self):
        now = datetime(2026, 9, 10, 14, 4, 12, tzinfo=timezone.utc)
        cid = make_poll_cycle_id(now)
        self.assertRegex(cid, r"^pc_20260910_1404_[0-9a-f]{6}$")

    def test_unique_across_calls_in_same_minute(self):
        now = datetime(2026, 9, 10, 14, 4, 12, tzinfo=timezone.utc)
        ids = {make_poll_cycle_id(now) for _ in range(50)}
        self.assertEqual(len(ids), 50)


class TestRun(unittest.TestCase):
    def _fixture(self, n):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for i in range(n):
                f.write(json.dumps({"contentId": str(i), "rawText": f"post {i}"}) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def _config(self, fixture_path, batch_size=3, loop=True):
        return Config(
            rabbitmq_host="unused", rabbitmq_port=5672, rabbitmq_user="u", rabbitmq_pass="p",
            exchange="trends.topic", fixture_path=fixture_path, source_id="src_test",
            batch_size=batch_size, poll_interval_seconds=0, loop_fixture=loop,
        )

    def test_publishes_batch_then_one_poll_completed_per_cycle(self):
        path = self._fixture(7)
        pub = FakePublisher()
        cycles = run(config=self._config(path, batch_size=3), publisher=pub, max_cycles=2)

        self.assertEqual(cycles, 2)
        raw = [m for rk, m in pub.published if rk == "content.raw.ingested"]
        completed = [m for rk, m in pub.published if rk == "source.poll.completed"]
        self.assertEqual(len(raw), 6)  # 2 cycles * batch_size 3
        self.assertEqual(len(completed), 2)
        for msg in completed:
            self.assertEqual(msg["itemCount"], 3)
            self.assertEqual(msg["sourceId"], "src_test")
            self.assertIn("pollCycleId", msg)
            self.assertIn("completedAt", msg)

    def test_logs_every_published_message_in_full(self):
        path = self._fixture(4)
        pub = FakePublisher()
        with self.assertLogs("te-connector", level="INFO") as logs:
            run(config=self._config(path, batch_size=2, loop=False), publisher=pub, max_cycles=1)
        lines = [l for l in logs.output if ":published " in l]
        self.assertEqual(len(lines), len(pub.published))      # 2 posts + 1 poll-completed
        for (rk, msg), line in zip(pub.published, lines):
            logged = line.split(f"published {rk} ", 1)[1]
            self.assertEqual(json.loads(logged), msg)

    def test_message_logging_can_be_turned_off(self):
        path = self._fixture(4)
        cfg = self._config(path, batch_size=2, loop=False)
        cfg = Config(**{**cfg.__dict__, "log_messages": False})
        with self.assertLogs("te-connector", level="INFO") as logs:
            run(config=cfg, publisher=FakePublisher(), max_cycles=1)
        self.assertFalse([l for l in logs.output if ":published " in l])
        self.assertTrue([l for l in logs.output if "poll cycle" in l])   # the summary stays

    def test_fills_in_default_source_id_without_overwriting_explicit_one(self):
        path = self._fixture(2)
        # second item already carries its own sourceId — run() must not clobber it
        with open(path, "a") as f:
            f.write(json.dumps({"contentId": "explicit", "sourceId": "src_other"}) + "\n")
        pub = FakePublisher()
        run(config=self._config(path, batch_size=3, loop=False), publisher=pub, max_cycles=1)
        raw = [m for rk, m in pub.published if rk == "content.raw.ingested"]
        by_id = {m["contentId"]: m["sourceId"] for m in raw}
        self.assertEqual(by_id["0"], "src_test")
        self.assertEqual(by_id["explicit"], "src_other")

    def test_does_not_close_externally_supplied_publisher(self):
        path = self._fixture(3)
        pub = FakePublisher()
        run(config=self._config(path, batch_size=3, loop=False), publisher=pub, max_cycles=1)
        self.assertFalse(pub.closed)

    def test_stops_after_max_cycles_even_when_looping(self):
        path = self._fixture(3)
        pub = FakePublisher()
        cycles = run(config=self._config(path, batch_size=2, loop=True), publisher=pub, max_cycles=5)
        self.assertEqual(cycles, 5)


    def test_stops_on_its_own_when_a_non_looping_fixture_runs_out(self):
        path = self._fixture(7)
        pub = FakePublisher()
        cycles = run(config=self._config(path, batch_size=3, loop=False), publisher=pub)
        self.assertEqual(cycles, 3)     # 3 + 3 + 1
        completed = [m for rk, m in pub.published if rk == "source.poll.completed"]
        self.assertEqual([m["itemCount"] for m in completed], [3, 3, 1])

    def test_a_failed_publish_surfaces_from_run(self):
        class Broken(FakePublisher):
            def publish(self, routing_key, message):
                raise ConnectionError("broker down")

        path = self._fixture(3)
        with self.assertRaises(ConnectionError):
            run(config=self._config(path, batch_size=3), publisher=Broken(), max_cycles=1)


if __name__ == "__main__":
    unittest.main()
