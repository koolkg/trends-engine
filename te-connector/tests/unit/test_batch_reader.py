import json
import os
import tempfile
import unittest

from connector.batch_reader import iter_batches, load_fixture


class TestLoadFixture(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines))
        self.addCleanup(os.remove, path)
        return path

    def test_parses_well_formed_lines(self):
        path = self._write([json.dumps({"contentId": "1"}), json.dumps({"contentId": "2"})])
        items = load_fixture(path)
        self.assertEqual([i["contentId"] for i in items], ["1", "2"])

    def test_skips_blank_lines(self):
        path = self._write([json.dumps({"contentId": "1"}), "", "   ", json.dumps({"contentId": "2"})])
        items = load_fixture(path)
        self.assertEqual(len(items), 2)

    def test_raises_with_line_number_on_bad_json(self):
        path = self._write([json.dumps({"contentId": "1"}), "{not valid json"])
        with self.assertRaises(ValueError) as ctx:
            load_fixture(path)
        self.assertIn(":2:", str(ctx.exception))

    def test_raises_on_empty_file(self):
        path = self._write([])
        with self.assertRaises(ValueError):
            load_fixture(path)

    def test_raises_on_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_fixture("/no/such/fixture.jsonl")


class TestIterBatches(unittest.TestCase):
    def setUp(self):
        self.items = [{"contentId": str(i)} for i in range(7)]

    def test_non_looping_slices_are_exact(self):
        batches = list(iter_batches(self.items, batch_size=3, loop=False))
        self.assertEqual([len(b) for b in batches], [3, 3, 1])
        self.assertEqual([i["contentId"] for i in batches[0]], ["0", "1", "2"])
        self.assertEqual([i["contentId"] for i in batches[-1]], ["6"])

    def test_non_looping_stops_after_items_exhausted(self):
        batches = list(iter_batches(self.items, batch_size=10, loop=False))
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 7)

    def test_looping_wraps_around(self):
        gen = iter_batches(self.items, batch_size=5, loop=True)
        first = next(gen)
        second = next(gen)
        self.assertEqual([i["contentId"] for i in first], ["0", "1", "2", "3", "4"])
        # wraps: items 5, 6, then back to 0, 1, 2
        self.assertEqual([i["contentId"] for i in second], ["5", "6", "0", "1", "2"])

    def test_looping_never_raises_stopiteration_across_many_batches(self):
        gen = iter_batches(self.items, batch_size=4, loop=True)
        for _ in range(20):
            batch = next(gen)
            self.assertEqual(len(batch), 4)

    def test_rejects_non_positive_batch_size(self):
        with self.assertRaises(ValueError):
            list(iter_batches(self.items, batch_size=0))

    def test_empty_items_yields_nothing(self):
        self.assertEqual(list(iter_batches([], batch_size=3, loop=True)), [])
        self.assertEqual(list(iter_batches([], batch_size=3, loop=False)), [])


if __name__ == "__main__":
    unittest.main()
