"""Shared test doubles: an in-memory Redis with the te-api client's interface,
a fake repository, and a canned feed payload shaped exactly like
te-aggregator's output."""
import json
import uuid

T1, T2, T3 = (str(uuid.uuid5(uuid.NAMESPACE_DNS, f"trend-{i}")) for i in (1, 2, 3))  # contain hex letters


def trend(trend_id, rank, name, category, **extra):
    t = {
        "trendId": trend_id, "rank": rank, "name": name, "summary": f"{name} summary", "category": category,
        "velocityScore": 9.5 - rank, "velocityLabel": "spike" if rank == 1 else "rising", "deltaPct": 400 - rank,
        "firstSeenAt": "2026-09-07T16:04:36Z", "sources": ["x", "tiktok"], "sparkline": [0, 1, 4, 9],
        "clusterId": "c-" + str(rank), "relatedTerms": ["#brandvoice"],
        "samplePosts": [{"text": "brands posting lowercase", "source": "x", "postedAt": "2026-09-07T19:10:00Z",
                         "author": "@a"}],
        "growth": [{"date": "2026-09-07T19:00:00Z", "score": 9}],
        "highlights": [f"{name} jumped to 9 posts in the 7 PM UTC hour (usually fewer than 1 an hour)."],
        "signals": [{"type": "term", "ref": name, "zScore": 8.65}], "signalKey": f"term:{name}",
    }
    t.update(extra)
    return t


def payload(version="20260907T200000000000Z"):
    return {"version": version, "computedAt": "2026-09-07T20:00:00Z",
            "trends": [trend(T1, 1, "#lowercase", "social"), trend(T2, 2, "#emailmarketing", "email"),
                       trend(T3, 3, '"girl dinner"', "content")]}


class DictRedis:
    def __init__(self, down=False):
        self.data, self.ttl, self.down = {}, {}, down

    def _check(self):
        if self.down:
            raise ConnectionError("redis down")

    def get(self, key):
        self._check()
        v = self.data.get(key)
        return v.encode() if isinstance(v, str) else v

    def set(self, key, value, ex=None, px=None, nx=False):
        self._check()
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    def seed(self, p, prefix="trends"):
        self.data[f"{prefix}:v:{p['version']}"] = json.dumps(p)
        self.data[f"{prefix}:current"] = p["version"]


class FakeRepo:
    def __init__(self, feed=None):
        self.feed, self.calls = feed, 0
        self.blocks = []  # campaign_draft_block rows: (id, trend_id, campaign_id, copy)

    def ranked_feed(self):
        self.calls += 1
        return self.feed

    def trend_detail(self, trend_id):
        for t in (self.feed or {}).get("trends", []):
            if t["trendId"] == trend_id:
                return t
        return None

    def insert_draft_block(self, block_id, trend_id, campaign_id, suggested_copy):
        self.blocks.append((block_id, trend_id, campaign_id, suggested_copy))


class FakeMailchimp:
    """MailchimpClient's interface; `fail` makes every call raise that exception."""

    def __init__(self, existing=(), fail=None):
        self.campaigns = {cid: {"id": cid} for cid in existing}
        self.content, self.calls, self.fail = {}, [], fail

    def _check(self, call):
        self.calls.append(call)
        if self.fail:
            raise self.fail

    def create_campaign(self, subject_line, preview_text, title, from_name, reply_to):
        self._check("create")
        cid = f"{len(self.campaigns):010x}"
        self.campaigns[cid] = {"id": cid, "settings": {"subject_line": subject_line, "title": title},
                               "long_archive_url": f"http://mailchimp.test/archive/{cid}"}
        return self.campaigns[cid]

    def get_campaign(self, campaign_id):
        self._check("get")
        if campaign_id not in self.campaigns:
            from api.mailchimp import MailchimpError
            raise MailchimpError(404, "Resource Not Found", "")
        return self.campaigns[campaign_id]

    def set_content(self, campaign_id, html, plain_text):
        self._check("content")
        self.content[campaign_id] = html
