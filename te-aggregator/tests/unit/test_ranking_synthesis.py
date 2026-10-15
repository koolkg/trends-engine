import unittest
from datetime import datetime, timedelta, timezone

from aggregator.ranking import Post, dedupe_windows, merge, score_signals, tag_share, top
from aggregator.synthesis import delta_pct, synthesize, top_phrases, top_tags, trend_id

NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)


def h(n):
    return NOW - timedelta(hours=n)


def row(ref, hours_ago, freq, base, z):
    return (ref, h(hours_ago + 1), h(hours_ago), freq, base, z)


def post(nid, text, hours_ago=1.0, source="x", category="social", engagement=10, author="@a"):
    return Post(nid, text, category, NOW - timedelta(hours=hours_ago), author, source, engagement)


class TestScoring(unittest.TestCase):
    def test_decayed_best_z_and_min_z_filter(self):
        w = dedupe_windows([row("#hot", 0, 10, 1, 5.0), row("#old", 3, 10, 1, 5.0), row("#flat", 0, 50, 48, 0.4)])
        c = {x.ref: x for x in score_signals("term", w, NOW, decay_per_hour=0.5, min_z=1.0)}
        self.assertEqual(c["#hot"].score, 5.0)          # ended just now: no decay
        self.assertEqual(c["#old"].score, 0.62)         # 5 * 0.5^3
        self.assertNotIn("#flat", c)                    # big but steady: never a candidate

    def test_dedupe_keeps_one_row_per_window(self):
        w = dedupe_windows([row("#a", 0, 3, 1, 2.0), row("#a", 0, 3, 1, 2.0), row("#a", 1, 2, 1, 1.0)])
        self.assertEqual(len(w["#a"]), 2)

    def test_tag_share(self):
        ps = [post(1, "x #Lowercase"), post(2, "y #lowercase #z"), post(3, "nothing"), post(4, "#lowercaser")]
        self.assertEqual(tag_share(ps, "#lowercase"), 0.5)


class TestMerge(unittest.TestCase):
    def setUp(self):
        w = dedupe_windows([row("#lowercase", 0, 10, 1, 6.0), row("#brandvoice", 0, 8, 1, 4.0),
                            row("#dupe", 1, 9, 2, 3.0)])
        self.terms = score_signals("term", w, NOW, 0.8, 1.0)
        cw = dedupe_windows([row("c-low", 0, 9, 1, 5.0), row("c-dinner", 0, 7, 1, 4.5)])
        self.clusters = score_signals("cluster", cw, NOW, 0.8, 1.0)
        shared = [post(i, f"brands in lowercase #lowercase #brandvoice {i}") for i in range(1, 7)]
        self.posts = {
            "term:#lowercase": shared,
            "term:#brandvoice": shared[:5],
            "term:#dupe": [post(20 + i, f"$12 dupe #dupe {i}") for i in range(4)],
            "cluster:c-low": shared[2:] + [post(30, "lowercase captions everywhere")],
            "cluster:c-dinner": [post(40 + i, f"girl dinner content {i}") for i in range(5)],
        }

    def test_tag_and_its_cluster_and_overlapping_tag_become_one_trend(self):
        trends = {t.key: t for t in merge(self.terms, self.clusters, self.posts)}
        low = trends["term:#lowercase"]
        self.assertEqual([t.ref for t in low.terms], ["#lowercase", "#brandvoice"])
        self.assertEqual([c.ref for c in low.clusters], ["c-low"])
        self.assertEqual(len(low.posts), 7)  # union, no duplicates
        self.assertNotIn("term:#brandvoice", trends)
        self.assertIn("cluster:c-dinner", trends)  # hashtag-less cluster stands alone
        self.assertIn("term:#dupe", trends)

    def test_top_orders_by_score(self):
        ranked = top(merge(self.terms, self.clusters, self.posts), 2)
        self.assertEqual([t.key for t in ranked], ["term:#lowercase", "cluster:c-dinner"])


class TestSynthesis(unittest.TestCase):
    def test_full_detail_for_a_merged_trend(self):
        w = dedupe_windows([row("#lowercase", 0, 10, 2, 6.0)])
        cw = dedupe_windows([row("c-low", 0, 9, 1, 5.0)])
        posts = {"term:#lowercase": [post(1, "brands posting lowercase #lowercase", 1.5, "x", "social", 50),
                                     post(2, "brands posting lowercase again #lowercase #brandvoice", 0.5, "tiktok",
                                          "social", 90),
                                     post(3, "brands posting lowercase too #lowercase #brandvoice", 0.2, "x",
                                          "content", 5)]}
        posts["cluster:c-low"] = posts["term:#lowercase"]
        trend, = merge(score_signals("term", w, NOW, 0.8, 1.0), score_signals("cluster", cw, NOW, 0.8, 1.0), posts)
        d = synthesize(trend, 1, NOW, sparkline_hours=4, spike_z=3.0)
        self.assertEqual(d["trendId"], trend_id("term:#lowercase"))
        self.assertEqual((d["name"], d["rank"], d["category"]), ("#lowercase", 1, "social"))
        self.assertEqual((d["velocityLabel"], d["deltaPct"], d["velocityScore"]), ("spike", 400, 6.0))
        self.assertEqual(d["sources"], ["x", "tiktok"])  # by post count
        self.assertEqual(d["sparkline"], [0, 0, 1, 2])
        self.assertEqual(d["growth"][-1], {"date": "2026-09-07T19:00:00Z", "score": 2})
        self.assertEqual(d["firstSeenAt"], "2026-09-07T18:30:00Z")
        self.assertEqual(d["clusterId"], "c-low")
        self.assertEqual(d["relatedTerms"], ["#brandvoice", "posting lowercase", "brands posting"])
        self.assertEqual([p["text"][:20] for p in d["samplePosts"]], ["brands posting lower"] * 3)
        self.assertEqual(d["samplePosts"][0]["source"], "tiktok")  # most engaged first
        self.assertEqual(d["summary"], "3 posts in the last 4 hours on X and TikTok, about 5 times its usual pace.")

    def test_cluster_only_trend_is_named_by_its_phrase(self):
        cw = dedupe_windows([row("c-d", 0, 5, 1, 2.0)])
        words = ["newsletter", "roundup", "carousel", "caption"]
        posts = {"cluster:c-d": [post(i, f"girl dinner {w}") for i, w in enumerate(words)]}
        trend, = merge([], score_signals("cluster", cw, NOW, 0.8, 1.0), posts)
        d = synthesize(trend, 3, NOW, 12, 3.0)
        self.assertEqual((d["name"], d["velocityLabel"]), ('"girl dinner"', "rising"))

    def test_phrase_ties_prefer_the_distinctive_phrase(self):
        # Every post repeats one sentence, so all its phrases tie. "content strategy" used to win
        # alphabetically; the trend's own catchphrase should.
        texts = [f"{o} girl dinner content strategy: half-finished ideas on a plate" for o in ("", "ok so", "psa:")]
        self.assertEqual(top_phrases(texts)[0], "girl dinner")
        self.assertNotIn("content strategy", top_phrases(texts, k=1))

    def test_more_frequent_phrase_still_wins_over_distinctive(self):
        texts = ["open rate jumped", "open rate again", "open rate up", "girl dinner once", "girl dinner twice"]
        self.assertEqual(top_phrases(texts, k=1, min_count=2), ["open rate"])

    def test_highlights_explain_the_trend_in_plain_words(self):
        w = dedupe_windows([row("#lowercase", 0, 26, 1.0, 14.8)])
        cw = dedupe_windows([row("c-low", 0, 5, 0.0, 4.9)])
        tagged = [post(i, f"brands posting lowercase {i} #lowercase", 0.5 + i * 0.3, src, author=f"@u{i}")
                  for i, src in enumerate(["x", "x", "tiktok", "instagram", "x", "reddit"])]
        untagged = [post(10 + i, f"every brand is typing in small letters now {i}", 0.4, "tiktok", author=f"@v{i}")
                    for i in range(3)]
        posts = {"term:#lowercase": tagged, "cluster:c-low": tagged[:3] + untagged}
        trend, = merge(score_signals("term", w, NOW, 0.8, 1.0), score_signals("cluster", cw, NOW, 0.8, 1.0), posts)
        hl = synthesize(trend, 1, NOW, sparkline_hours=4, spike_z=3.0)["highlights"]
        self.assertEqual(hl[0], "#lowercase jumped to 26 posts in the 7 PM UTC hour (usually about 1 an hour).")
        self.assertEqual(hl[1], "Spiking right now.")
        self.assertEqual(hl[2], "Started on Reddit, then spread to X, Instagram and TikTok within the same hour.")
        self.assertEqual(hl[3], "9 different accounts are posting about it, so it isn't one account pushing it.")
        self.assertEqual(hl[4], "3 more posts talk about it in their own words, without the hashtag.")
        for line in hl:
            self.assertNotIn("z-score", line)

    def test_highlights_for_cooling_one_platform_cluster_only(self):
        cw = dedupe_windows([row("c-x", 3, 6, 2.4, 2.0)])
        posts = {"cluster:c-x": [post(i, f"same idea said differently {i}", 3.5, "instagram") for i in range(6)]
                 + [post(9, "same idea once more", 0.5, "instagram")]}
        trend, = merge([], score_signals("cluster", cw, NOW, 0.8, 1.0), posts)
        hl = synthesize(trend, 2, NOW, sparkline_hours=4, spike_z=3.0)["highlights"]
        self.assertEqual(hl[0], "Posts about it jumped to 6 posts in the 4 PM UTC hour (usually about 2 an hour).")
        self.assertEqual(hl[1], "Cooling off since its peak around 4 PM UTC.")
        self.assertEqual(hl[2], "Only on Instagram so far.")
        self.assertIn("no shared hashtag", hl[-1])
        self.assertFalse(any("accounts" in line for line in hl))  # one author: no "many voices" claim

    def test_plain_summary(self):
        from aggregator.synthesis import plain_summary
        self.assertEqual(plain_summary(84, 12, ["x", "tiktok", "instagram", "reddit"], 984),
                         "84 posts in the last 12 hours on X, TikTok, Instagram and Reddit, about 11 times its usual pace.")
        self.assertEqual(plain_summary(1, 12, ["reddit"], 40), "1 post in the last 12 hours on Reddit, above its usual pace.")
        self.assertEqual(plain_summary(9, 12, [], -38), "9 posts in the last 12 hours, below its usual pace.")

    def test_trend_ids_are_stable(self):
        self.assertEqual(trend_id("term:#x"), trend_id("term:#x"))
        self.assertNotEqual(trend_id("term:#x"), trend_id("term:#y"))

    def test_helpers(self):
        self.assertEqual(delta_pct(10, 0.2), 980)   # baseline floored at 1
        self.assertEqual(delta_pct(3, 4), -25)
        self.assertEqual(top_phrases(["a b", "c d"]), [])
        self.assertEqual(top_tags(["#xx #fyp", "#xx #yy", "#xx"], exclude="#yy"), ["#xx"])  # generic + excluded dropped


if __name__ == "__main__":
    unittest.main()
