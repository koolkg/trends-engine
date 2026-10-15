#!/usr/bin/env python3
"""Generates fixtures/raw_content.jsonl (+ a ground-truth labels file) for
te-connector to replay.

Synthetic but cohesive: the same handful of marketing trends show up on
Reddit, X, Instagram and TikTok, each starting on one platform and spreading
to the others after a lag. That's what lets downstream stages (te-detector,
te-cluster, te-aggregator) find a trend *across* platforms rather than
per-platform. Mixed in:
  - an evergreen baseline of relevant-but-not-trending marketing chatter,
  - off-topic noise (for te-normalizer's relevance filter to drop),
  - exact reposts and near-duplicate cross-posts (for its dedup step),
  - one trend with no shared hashtag at all ("girl dinner"-style micro-content),
    so only semantic clustering (te-cluster), not hashtag counting, can find it,
  - one declining trend and one sharp late-week spike.

The post text lives in post_texts.py: each trend is one idea written many
different ways by different kinds of people (30-50 distinct posts per trend,
each used only a few times), so trends have to be found by meaning, not by
repeated sentences. Background chatter reuses trend vocabulary and the noise
includes look-alikes (food "girl dinner", pet "POV" posts).

Every record matches the content.raw.ingested schema
exactly. Ground truth (which trend each post belongs to, which posts are
duplicates of which) goes to a separate labels file so the replayed messages
stay schema-clean — use it to evaluate relevance/dedup/cluster accuracy.

Deterministic: same --seed gives byte-identical output on any Python 3
(see Rng below). Stdlib only.

    python fixtures/generate_fixture.py                # defaults below
    python fixtures/generate_fixture.py --count 3000 --days 7   # sparser, week-long span
"""
import argparse
import bisect
import json
import math
import random
import re
import sys
from datetime import datetime, timedelta, timezone

from post_texts import EVERGREEN_TEXTS, NOISE_TEXTS, TREND_TEXTS

START = datetime(2026, 9, 7, tzinfo=timezone.utc)
WEEK_H = 168.0  # trend timings below are written against a 7-day span, then scaled to --days


class Rng:
    """Built only on random.Random.random() — the raw Mersenne Twister output,
    which (like int seeding) is identical across Python versions. choices(),
    gauss(), randint() etc. have changed implementation between releases, so
    they're reimplemented here to keep the committed fixture reproducible."""

    def __init__(self, seed):
        self._r = random.Random(seed)

    def random(self):
        return self._r.random()

    def uniform(self, a, b):
        return a + (b - a) * self.random()

    def randint(self, a, b):
        return a + int(self.random() * (b - a + 1))

    def choice(self, seq):
        return seq[int(self.random() * len(seq))]

    def chance(self, p):
        return self.random() < p

    def sample(self, seq, k):
        pool, out = list(seq), []
        for _ in range(min(k, len(pool))):
            out.append(pool.pop(int(self.random() * len(pool))))
        return out

    def weighted_index(self, cumulative):
        return bisect.bisect_right(cumulative, self.random() * cumulative[-1])

    def lognormal(self, mu, sigma):
        u1, u2 = self.random() or 1e-12, self.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return math.exp(mu + sigma * z)


def cumulative(weights):
    out, total = [], 0.0
    for w in weights:
        total += w
        out.append(total)
    return out


# --------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------

PLATFORMS = ["reddit", "x", "instagram", "tiktok"]
SOURCE_ID = {
    "reddit": "src_reddit_subreddits",
    "x": "src_x_search",
    "instagram": "src_instagram_hashtag",
    "tiktok": "src_tiktok_hashtag",
}
# Rough per-platform engagement shape: (log-median likes, sigma, comments ratio, shares ratio)
ENGAGEMENT = {
    "reddit": (4.0, 1.2, (0.10, 0.60), (0.00, 0.03)),
    "x": (4.5, 1.5, (0.02, 0.08), (0.05, 0.20)),
    "instagram": (5.5, 1.3, (0.01, 0.05), (0.005, 0.03)),
    "tiktok": (6.3, 1.6, (0.005, 0.03), (0.02, 0.08)),
}
BACKGROUND_PLATFORM_WEIGHTS = {"reddit": 0.30, "x": 0.30, "instagram": 0.20, "tiktok": 0.20}

HANDLE_A = ["growth", "copy", "brand", "social", "email", "content", "beauty", "budget", "thrifty",
            "swatch", "unbox", "saas", "smallbiz", "creative", "daily", "just", "notso", "the",
            "marketing", "retail", "shop", "indie", "caffeinated", "late", "honest", "tiny"]
HANDLE_B = ["megs", "gal", "guy", "queen", "nerd", "lab", "club", "diaries", "hq", "notes", "sam",
            "alex", "jordan", "writes", "talks", "finds", "maven", "desk", "files", "studio",
            "collective", "chronicles", "mode", "works"]
EMOJI = ["✨", "🔥", "💸", "📦", "👀", "🙌", "💯", "😅", "📈", "💌", "🛍️", "🤯"]

SUBREDDITS = {
    "content": ["socialmedia", "marketing", "content_marketing"],
    "social": ["socialmedia", "marketing", "TikTokCringe"],
    "ecommerce": ["BeautyGuruChatter", "MakeupAddiction", "ecommerce"],
    "email": ["emailmarketing", "marketing", "smallbusiness"],
    "evergreen": ["marketing", "smallbusiness", "socialmedia", "emailmarketing", "Entrepreneur"],
    "noise": ["pics", "AskReddit", "Baking", "aww", "CasualConversation"],
}
GENERIC_TAGS = {
    "content": ["#contentcreator", "#socialmediatips", "#marketing", "#creatoreconomy"],
    "social": ["#marketing", "#smallbusiness", "#ugc"],
    "ecommerce": ["#beauty", "#makeup", "#skincare"],
    "email": ["#marketingtips", "#smallbusiness"],
    "evergreen": ["#marketing", "#smallbusiness", "#entrepreneur", "#digitalmarketing"],
    "noise": ["#life", "#weekend", "#mood"],
}

# --------------------------------------------------------------------------
# Slot values shared by all templates
# --------------------------------------------------------------------------

BEAUTY_PRODUCTS = ["serum", "primer", "setting spray", "lip oil", "foundation", "concealer",
                   "moisturizer", "perfume", "blush", "bronzer", "eye cream", "SPF"]
GENERAL_PRODUCTS = ["standing desk", "espresso machine", "planner", "water bottle", "sneakers",
                    "candle", "headphones", "skincare set", "tote bag", "desk lamp"]
RETAILERS = ["Target", "Walmart", "the drugstore", "Amazon", "Aldi", "TJ Maxx", "Trader Joe's"]
FIRST_NAMES = ["Maya", "Chris", "Priya", "Dev", "Hannah", "Luis", "Grace", "Omar", "Tess", "Ben"]
BRANDS = ["Fernbrook", "Otter & Oak", "Lumen Supply", "Tidewell", "Pantry Club",
          "Northstar Candle Co.", "Kiln & Co", "Hearth Goods", "Saltbox"]
THINGS = ["holiday menu", "home office", "morning routine", "garden", "next launch", "wardrobe"]
PLATFORM_NAMES = ["Instagram", "TikTok", "LinkedIn", "X", "Pinterest"]


def slots(rng):
    return {
        "p_lo": rng.randint(6, 22),
        "p_hi": rng.randint(38, 120),
        "product": rng.choice(BEAUTY_PRODUCTS),
        "gproduct": rng.choice(GENERAL_PRODUCTS),
        "retailer": rng.choice(RETAILERS),
        "pct": rng.randint(12, 48),
        "pct2": rng.randint(8, 31),
        "n": rng.randint(2, 9),
        "k": rng.randint(2, 40),
        "name": rng.choice(FIRST_NAMES),
        "brand": rng.choice(BRANDS),
        "thing": rng.choice(THINGS),
        "pname": rng.choice(PLATFORM_NAMES),
    }


# --------------------------------------------------------------------------
# Trend curves — hours since START (already shifted by the platform's lag)
# --------------------------------------------------------------------------

def rise(t0, k, floor=0.03):
    return lambda x: floor + 1.0 / (1.0 + math.exp(-(x - t0) / k))


def spike(t0, s, floor=0.02):
    return lambda x: floor + math.exp(-0.5 * ((x - t0) / s) ** 2)


def decline(tau, floor=0.05):
    return lambda x: floor + math.exp(-max(x, 0.0) / tau)


def steady(floor=0.25):
    return lambda x: floor + (1.0 - floor) * max(x, 0.0) / WEEK_H


def diurnal(hour_of_day):
    # US-centric activity: peaks ~21:00 UTC (early afternoon Pacific), troughs ~09:00 UTC.
    return 0.4 + 0.6 * (0.5 + 0.5 * math.cos(2 * math.pi * (hour_of_day - 21) / 24.0))


# --------------------------------------------------------------------------
# Trends. platforms: {platform: (share of this trend's posts, lag hours after origin)}
# Timings are in hours of a 7-day week and scaled to --days at runtime.
# --------------------------------------------------------------------------

TRENDS = [
    {
        "id": "girl_dinner_microcontent",
        "category": "content",
        "share": 0.15,
        "curve": ("rise", 80, 12),
        "platforms": {"tiktok": (0.35, 0), "x": (0.25, 6), "instagram": (0.25, 14), "reddit": (0.15, 26)},
        "hashtags": [],  # deliberately none: only semantic clustering can find this one
    },
    {
        "id": "dupe_culture",
        "category": "ecommerce",
        "share": 0.20,
        "curve": ("rise", 40, 16),
        "platforms": {"tiktok": (0.30, 0), "reddit": (0.30, 4), "instagram": (0.25, 10), "x": (0.15, 8)},
        "hashtags": ["#dupe", "#dupes", "#dupealert", "#budgetbeauty", "#affordablemakeup"],
    },
    {
        "id": "pov_product_demos",
        "category": "social",
        "share": 0.12,
        "curve": ("steady",),
        "platforms": {"tiktok": (0.40, 0), "instagram": (0.35, 6), "x": (0.15, 12), "reddit": (0.10, 24)},
        "hashtags": ["#POV", "#unboxing", "#productdemo", "#ugc"],
    },
    {
        "id": "numbers_subject_lines",
        "category": "email",
        "share": 0.12,
        "curve": ("rise", 60, 20),
        "platforms": {"x": (0.40, 0), "reddit": (0.35, 8), "instagram": (0.20, 18), "tiktok": (0.05, 30)},
        "hashtags": ["#emailmarketing", "#subjectlines", "#emailmarketingtips"],
    },
    {
        "id": "quiet_luxury_fatigue",
        "category": "content",
        "share": 0.13,
        "curve": ("decline", 60),
        "platforms": {"instagram": (0.40, 0), "x": (0.25, 0), "tiktok": (0.20, 0), "reddit": (0.15, 0)},
        "hashtags": ["#quietluxury", "#oldmoney", "#minimalist"],
    },
    {
        "id": "founder_voice_cold_opens",
        "category": "email",
        "share": 0.10,
        "curve": ("rise", 100, 14),
        "platforms": {"reddit": (0.35, 0), "x": (0.35, 4), "instagram": (0.25, 12), "tiktok": (0.05, 24)},
        "hashtags": ["#founderstory", "#emailmarketing", "#smallbusiness"],
    },
    {
        "id": "lowercase_brand_voice",
        "category": "social",
        "share": 0.18,
        "curve": ("spike", 134, 6),
        "platforms": {"x": (0.35, 0), "tiktok": (0.30, 4), "instagram": (0.25, 8), "reddit": (0.10, 18)},
        "hashtags": ["#lowercase", "#brandvoice"],
        "lowercase": 0.7,
    },
]

# --------------------------------------------------------------------------
# Voice. A light opener/closer sometimes wraps the post, the way people
# naturally vary how they say the same thing. Most posts get neither. Real
# duplicates (reposts / cross-posts below) reuse the *whole* final text.
# --------------------------------------------------------------------------

OPENERS = ["", "", "", "", "", "", "", "", "ok so ", "honestly ", "update: ", "not gonna lie, ",
           "real talk, ", "quick one: ", "okay but ", "lol "]
# A closer that's a phrase becomes its own sentence ("... done. thoughts?"); emoji just trail.
CLOSERS = ["", "", "", "", "", "", "", "", "", "", "thoughts?", "curious if it's just us",
           "more soon", "anyway", "will report back", " 👀", " lol", " 😅", " 🙃"]
NOISE_CLOSERS = ["", "", "", "", " lol", " 😅", " ugh", "anyway", "happy friday", "wish me luck"]


class Deck:
    """Hands out a pool's texts in a shuffled order, every text once before
    any repeats, so each post idea is used about equally often (2-5 times at
    the default size) instead of some 10 times and others never."""

    def __init__(self, rng, texts):
        self.rng, self.texts, self.order = rng, list(texts), []

    def draw(self):
        if not self.order:
            self.order = self.rng.sample(self.texts, len(self.texts))
        return self.order.pop()


def words_key(text):
    """The words of a post, the way te-normalizer's dedup compares them
    (lowercase; hashtags, mentions, emoji and punctuation ignored)."""
    key = re.sub(r"[#@]\w+", " ", text.lower())
    key = re.sub(r"[^\w\s$%]", " ", key)
    return " ".join(key.split())


def voiced(rng, template, seen, closers=CLOSERS, openers=OPENERS):
    """Fills the template's placeholders and maybe adds an opener/closer.
    Re-rolls if an earlier post already has the same words: independent
    authors shouldn't collide into what dedup would treat as a repost."""
    for _ in range(20):
        opener, closer = rng.choice(openers), rng.choice(closers)
        if template[:1].isupper() or template.startswith(("'", "POV", "hot take")) or "?" in template:
            opener = ""  # "honestly Are POV ads..." reads wrong: openers only go in front of plain statements
        if closer and not closer.startswith(" "):
            closer = (" " if template.rstrip()[-1] in ".?!" else ". ") + closer
        text = (opener + template + closer).format(**slots(rng))
        if words_key(text) not in seen:
            break
    seen.add(words_key(text))
    return text


# --------------------------------------------------------------------------
# Record construction
# --------------------------------------------------------------------------

B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def make_content_id(rng, platform):
    if platform == "reddit":
        return "t3_" + "".join(rng.choice(B36) for _ in range(7))
    if platform == "instagram":
        return "".join(rng.choice(B64URL) for _ in range(11))
    prefix = "18" if platform == "x" else "74"
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(17))


def make_handle(rng, platform):
    sep = "" if platform == "x" else rng.choice(["", "", "_", "."] if platform != "reddit" else ["", "_", "-"])
    name = rng.choice(HANDLE_A) + sep + rng.choice(HANDLE_B)
    if rng.chance(0.35):
        name += str(rng.randint(1, 99))
    return ("u/" if platform == "reddit" else "@") + name


def make_url(platform, content_id, author, subreddit):
    if platform == "reddit":
        return f"https://www.reddit.com/r/{subreddit}/comments/{content_id[3:]}/"
    if platform == "x":
        return f"https://x.com/{author[1:]}/status/{content_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/p/{content_id}/"
    return f"https://www.tiktok.com/{author}/video/{content_id}"


def _ends_with_emoji(text):
    return bool(text) and ord(text.rstrip()[-1]) > 0x2000


def decorate(rng, platform, core, category, trend_tags, lowercase_p=0.0):
    """Wraps a platform-agnostic core text in that platform's conventions.
    The shared core is what keeps a trend cohesive across platforms."""
    text = core
    if rng.chance(lowercase_p):
        text = text.lower()
    tags = list(trend_tags)
    generic = GENERIC_TAGS[category]
    if platform == "reddit":
        lead = rng.choice(["Genuine question: ", "Discussion: "] if "?" in text else
                          ["Anyone else noticing this? ", "Discussion: ", "PSA: ", "Hot take: "])
        plain_start = text[:1].islower() and not text.startswith(tuple(o for o in OPENERS if o))
        if category not in ("noise",) and plain_start and rng.chance(0.3):
            text = lead + text
        if rng.chance(0.25):
            signoff = rng.choice(["Curious what you all think.", "Would love to hear if others see the same.",
                                  "Thoughts?"])
            if not text.rstrip().endswith("?"):  # a question already invites replies
                text += (" " if text.rstrip()[-1:] in ".!" else ". ") + signoff
        return text
    if platform == "x":
        picked = rng.sample(tags, rng.choice([0, 1, 1, 2])) if tags else []
        if picked:
            text += " " + " ".join(picked)
        if rng.chance(0.05):
            text += " 🧵"
        return text
    if platform == "instagram":
        if rng.chance(0.6) and not _ends_with_emoji(text):
            text += " " + rng.choice(EMOJI)
        picked = rng.sample(tags, rng.randint(1, 3)) + rng.sample(generic, rng.randint(1, 3))
        return text + "\n\n" + " ".join(dict.fromkeys(picked))  # a tag can be both trend and generic (#ugc)
    # tiktok
    if rng.chance(0.4) and not _ends_with_emoji(text):
        text += " " + rng.choice(EMOJI)
    picked = rng.sample(tags, rng.randint(1, 2)) + rng.sample(generic, 1)
    if rng.chance(0.6):
        picked.append(rng.choice(["#fyp", "#foryou", "#foryoupage"]))
    return text + " " + " ".join(dict.fromkeys(picked))


def engagement(rng, platform, multiplier):
    mu, sigma, (c_lo, c_hi), (s_lo, s_hi) = ENGAGEMENT[platform]
    likes = max(0, int(rng.lognormal(mu, sigma) * multiplier))
    return {
        "likes": likes,
        "shares": int(likes * rng.uniform(s_lo, s_hi)),
        "comments": int(likes * rng.uniform(c_lo, c_hi)),
    }


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_curve(spec, scale):
    kind = spec[0]
    if kind == "rise":
        return rise(spec[1] * scale, spec[2] * scale)
    if kind == "spike":
        return spike(spec[1] * scale, spec[2] * scale)
    if kind == "decline":
        return decline(spec[1] * scale)
    return steady()


def generate(count, days, seed):
    rng = Rng(seed)
    hours = int(days * 24)
    scale = hours / WEEK_H
    authors = {p: [make_handle(rng, p) for _ in range(250)] for p in PLATFORMS}
    used_ids = set()
    records = []  # (record, label, core, category, tags, lowercase_p)

    def new_id(platform):
        while True:
            cid = make_content_id(rng, platform)
            if cid not in used_ids:
                used_ids.add(cid)
                return cid

    def emit(platform, hour_idx, core, category, tags, label, multiplier, lowercase_p=0.0, after=None):
        if after is None:
            posted = START + timedelta(hours=hour_idx, seconds=rng.randint(0, 3599))
        else:
            posted = after
        author = rng.choice(authors[platform])
        subreddit = rng.choice(SUBREDDITS[category]) if platform == "reddit" else None
        cid = new_id(platform)
        rec = {
            "sourceId": SOURCE_ID[platform],
            "sourceType": "api",
            "contentId": cid,
            "rawText": decorate(rng, platform, core, category, tags, lowercase_p),
            "author": author,
            "postedAt": iso(posted),
            "engagement": engagement(rng, platform, multiplier),
            "url": make_url(platform, cid, author, subreddit),
        }
        records.append((rec, label, core, category, tags, lowercase_p, posted))
        return rec

    diurnal_w = [diurnal(h % 24) for h in range(hours)]
    background_cum = cumulative(diurnal_w)
    bg_platforms = list(BACKGROUND_PLATFORM_WEIGHTS)
    bg_platform_cum = cumulative([BACKGROUND_PLATFORM_WEIGHTS[p] for p in bg_platforms])

    n_noise = round(count * 0.12)
    n_evergreen = round(count * 0.23)
    n_trend = count - n_noise - n_evergreen

    seen = set()  # every core text so far (lowercased), to keep independent posts distinct
    for texts, n, category, kind, mult in ((NOISE_TEXTS, n_noise, "noise", "noise", 0.25),
                                           (EVERGREEN_TEXTS, n_evergreen, "evergreen", "evergreen", 0.6)):
        deck = Deck(rng, texts)
        for _ in range(n):
            platform = bg_platforms[rng.weighted_index(bg_platform_cum)]
            hour_idx = rng.weighted_index(background_cum)
            template = deck.draw()
            core = (voiced(rng, template, seen, NOISE_CLOSERS, [""]) if kind == "noise"
                    else voiced(rng, template, seen))
            emit(platform, hour_idx, core, category, [], {"kind": kind, "trend": None}, mult)

    for trend in TRENDS:
        n = round(n_trend * trend["share"])
        curve = build_curve(trend["curve"], scale)
        plats = list(trend["platforms"])
        plat_cum = cumulative([trend["platforms"][p][0] for p in plats])
        hour_cum, peak = {}, {}
        for p in plats:
            lag = trend["platforms"][p][1] * scale
            w = [curve(h - lag) * diurnal_w[h] for h in range(hours)]
            hour_cum[p] = cumulative(w)
            peak[p] = max(curve(h - lag) for h in range(hours))
        deck = Deck(rng, TREND_TEXTS[trend["id"]])
        for _ in range(n):
            p = plats[rng.weighted_index(plat_cum)]
            hour_idx = rng.weighted_index(hour_cum[p])
            intensity = curve(hour_idx - trend["platforms"][p][1] * scale) / peak[p]
            core = voiced(rng, deck.draw(), seen)
            emit(p, hour_idx, core, trend["category"], trend["hashtags"],
                 {"kind": "trend", "trend": trend["id"]}, 0.8 + 2.5 * intensity, trend.get("lowercase", 0.0))

    # Duplicates, drawn from trend posts: near-dup cross-posts (same core text,
    # re-decorated for a different platform) and exact reposts (identical rawText).
    end = START + timedelta(hours=hours)
    trend_rows = [r for r in records if r[1]["kind"] == "trend"]
    for kind, n_dups in (("near", round(count * 0.025)), ("exact", round(count * 0.008))):
        for orig in rng.sample(trend_rows, n_dups):
            rec, label, core, category, tags, lowercase_p, posted = orig
            lag_min = max(5, int(rng.randint(30, 720) * scale))  # reposts land minutes-to-hours later
            when = min(posted + timedelta(minutes=lag_min), end - timedelta(seconds=1))
            dup_label = dict(label, duplicateOf=rec["contentId"], duplicateKind=kind)
            if kind == "near":
                platform = rng.choice([p for p in PLATFORMS if SOURCE_ID[p] != rec["sourceId"]])
                emit(platform, None, core, category, tags, dup_label, 0.7, lowercase_p, after=when)
            else:
                platform = next(p for p in PLATFORMS if SOURCE_ID[p] == rec["sourceId"])
                dup = emit(platform, None, core, category, tags, dup_label, 0.4, lowercase_p, after=when)
                dup["rawText"] = rec["rawText"]

    records.sort(key=lambda r: (r[0]["postedAt"], r[0]["contentId"]))
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=1400, help="base posts before duplicates (default 1400)")
    ap.add_argument("--days", type=float, default=1,
                    help="span of postedAt timestamps in days (default 1 = 24h, dense enough for "
                         "te-detector's 5-minute windows; trend timings and lags scale with it)")
    ap.add_argument("--seed", type=int, default=20260907)
    ap.add_argument("--out", default="fixtures/raw_content.jsonl")
    ap.add_argument("--labels", default="fixtures/raw_content.labels.jsonl")
    args = ap.parse_args(argv)

    records = generate(args.count, args.days, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec, *_ in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(args.labels, "w", encoding="utf-8") as f:
        for rec, label, *_ in records:
            row = {"contentId": rec["contentId"], "sourceId": rec["sourceId"], "postedAt": rec["postedAt"]}
            row.update({"kind": label["kind"], "trend": label["trend"],
                        "duplicateOf": label.get("duplicateOf"), "duplicateKind": label.get("duplicateKind")})
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Summary to stderr
    by = {}
    for rec, label, *_ in records:
        key = label["trend"] or label["kind"]
        plat = next(p for p in PLATFORMS if SOURCE_ID[p] == rec["sourceId"])
        by.setdefault(key, {}).setdefault(plat, 0)
        by[key][plat] += 1
    print(f"wrote {len(records)} records to {args.out} (+ labels to {args.labels})", file=sys.stderr)
    print(f"{'':28}" + "".join(f"{p:>10}" for p in PLATFORMS) + f"{'total':>8}", file=sys.stderr)
    for key in sorted(by):
        row = by[key]
        print(f"{key:28}" + "".join(f"{row.get(p, 0):>10}" for p in PLATFORMS) + f"{sum(row.values()):>8}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
