import json
import unittest
from datetime import datetime, timezone

from normalizer.pipeline import (DUPLICATE, IRRELEVANT, PUBLISHED, InvalidMessage, parse, process)
from normalizer.store import SaveResult

NOW = datetime(2026, 9, 7, 12, 0, 5, tzinfo=timezone.utc)


class FakeStore:
    """Mimics Store.save, including the unique-index dedup on dedup_hash."""

    def __init__(self):
        self.raw, self.normalized, self._next = [], {}, 1

    def save(self, item, cleaned, dedup_hash, relevant, score, category):
        raw_id = self._next; self._next += 1
        self.raw.append((raw_id, item, cleaned))
        normalized_id = None
        if relevant and dedup_hash not in self.normalized:
            normalized_id = 1000 + raw_id
            self.normalized[dedup_hash] = (normalized_id, cleaned, score, category)
        return SaveResult(raw_id, NOW, normalized_id, NOW if normalized_id else None)


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, routing_key, message):
        self.published.append((routing_key, message))


def msg(**overrides):
    base = {
        "sourceId": "src_x_search", "sourceType": "api", "contentId": "1800000000000000001",
        "rawText": "found the $12 dupe for the $58 serum 🔥 #dupe https://t.co/x",
        "author": "@beautyonabudget", "postedAt": "2026-09-07T11:58:00Z",
        "engagement": {"likes": 41000, "shares": 2100, "comments": 640}, "url": "https://x.com/a/status/1",
    }
    base.update(overrides)
    return json.dumps(base).encode()


class TestParse(unittest.TestCase):
    def test_parses_schema(self):
        item = parse(msg())
        self.assertEqual((item.source_id, item.content_id, item.likes, item.comments),
                         ("src_x_search", "1800000000000000001", 41000, 640))
        self.assertEqual(item.posted_at, datetime(2026, 9, 7, 11, 58, tzinfo=timezone.utc))

    def test_rejects_non_json_and_missing_fields(self):
        for body in (b"not json", b"[1,2]", msg(rawText=""), msg(sourceId=None)):
            with self.subTest(body=body[:30]):
                with self.assertRaises(InvalidMessage):
                    parse(body)

    def test_tolerates_missing_optional_fields(self):
        body = json.dumps({"sourceId": "s", "contentId": "c", "rawText": "t"}).encode()
        item = parse(body)
        self.assertEqual((item.source_type, item.author, item.posted_at, item.likes), ("unknown", None, None, 0))

    def test_bad_timestamp_and_engagement_degrade_gracefully(self):
        item = parse(msg(postedAt="yesterday", engagement={"likes": "lots", "shares": -5}))
        self.assertIsNone(item.posted_at)
        self.assertEqual((item.likes, item.shares, item.comments), (0, 0, 0))


class TestProcess(unittest.TestCase):
    def setUp(self):
        self.store, self.pub = FakeStore(), FakePublisher()

    def run_one(self, body):
        return process(body, self.store, self.pub, threshold=0.5)

    def test_relevant_post_is_stored_and_published(self):
        self.assertEqual(self.run_one(msg()), PUBLISHED)
        self.assertEqual(len(self.store.raw), 1)
        (routing_key, out), = self.pub.published
        self.assertEqual(routing_key, "content.normalized")
        self.assertEqual(out["category"], "ecommerce")
        self.assertEqual(out["cleanedText"], "found the $12 dupe for the $58 serum 🔥 #dupe")
        self.assertEqual((out["normalizedId"], out["rawContentId"], out["contentId"]), (1001, 1, "1800000000000000001"))
        self.assertEqual(out["postedAt"], "2026-09-07T11:58:00Z")
        self.assertEqual(out["processedAt"], "2026-09-07T12:00:05Z")

    def test_irrelevant_post_is_archived_but_not_published(self):
        self.assertEqual(self.run_one(msg(rawText="just adopted a very good dog, no notes")), IRRELEVANT)
        self.assertEqual(len(self.store.raw), 1)  # raw archive: always written
        self.assertEqual(self.store.normalized, {})
        self.assertEqual(self.pub.published, [])

    def test_cross_post_with_different_hashtags_is_a_duplicate(self):
        self.assertEqual(self.run_one(msg()), PUBLISHED)
        repost = msg(sourceId="src_instagram_hashtag", contentId="abc",
                     rawText="Found the $12 dupe for the $58 serum!\n\n#budgetbeauty #dupes ✨")
        self.assertEqual(self.run_one(repost), DUPLICATE)
        self.assertEqual(len(self.store.raw), 2)  # both archived
        self.assertEqual(len(self.pub.published), 1)  # published once

    def test_invalid_message_raises_and_touches_nothing(self):
        with self.assertRaises(InvalidMessage):
            self.run_one(b"{}")
        self.assertEqual((self.store.raw, self.pub.published), ([], []))


if __name__ == "__main__":
    unittest.main()
