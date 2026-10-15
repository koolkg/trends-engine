#!/usr/bin/env python3
"""Generates fixtures/raw_content_v2.jsonl (+ a ground-truth labels file).

v2 is the realistic-volume companion to v1 (generate_fixture.py, unchanged).
Same record schema, same three-way labels, so te-connector replays it with no
code change (CONNECTOR_FIXTURE_PATH=fixtures/raw_content_v2.jsonl) and the
eval tooling reads its labels the same way. What changes is the shape of the
data:

  - volume: --per-day posts (default 8,000) over --days (default 5), with a
    day/night curve and quieter weekends, instead of ~1,400 in one day
  - low signal: planted trends are ~6% of posts; most posts are everyday
    noise (~76%) and evergreen marketing talk (~14%), not trends
  - long-tail hashtags: ~2,400 tags drawn with Zipf weights, so a few are
    everywhere and most are rare; Reddit posts almost never use them
  - three platforms: X, Reddit, Instagram (no TikTok: no licensed raw data)
  - personas: text written in different voices (shopper, creator, marketer,
    small business, skeptic, brand, press), with openers, closers, casing,
    emoji and occasional typos per persona; Reddit posts get a title and body
  - traps: off-topic look-alikes, spam, non-English posts, exact reposts and
    near-duplicate cross-posts, and one bot-amplified "trend" (a giveaway
    pushed by a handful of accounts)
  - trend shapes over several days: a sharp spike, slow burns, a decline,
    two waves, a steady one, one with no hashtag, and one emerging at the end

Post text lives in post_texts_v2.py (written once from personas by an LLM and
kept in the repo, so this script stays deterministic and stdlib-only).

Labels: kind is trend | evergreen | noise as in v1 (spam, look-alikes and
non-English posts are noise; the amplified giveaway is evergreen marketing),
plus subkind, persona and lang for finer evaluation.

    python fixtures/generate_fixture_v2.py                     # defaults
    python fixtures/generate_fixture_v2.py --days 7 --per-day 20000
"""
import argparse
import bisect
import json
import math
import random
import re
import sys
from datetime import datetime, timedelta, timezone

import post_texts_v2 as T

def start_for(days):
    """The posts run backwards from now: the last hour is the one that just ended
    (UTC), the first is --days before it. Nothing is ever dated in the future."""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return end - timedelta(hours=int(days * 24))


# Set from --days in main(). The day/night rhythm and the weekend dip follow the
# real clock, so they land on the actual evenings and Saturdays/Sundays.
START = start_for(5)
REF_H = 120.0  # trend timings below are written against a 5-day span, then scaled to --days

MIX = {  # share of base posts (before duplicates)
    "noise": 0.759, "evergreen": 0.14, "lookalike": 0.015, "spam": 0.015, "foreign": 0.005,
    "trend": 0.06, "amplified": 0.006,
}
PLATFORMS = ["x", "reddit", "instagram"]
SOURCE_ID = {"x": "src_x_search", "reddit": "src_reddit_subreddits", "instagram": "src_instagram_hashtag"}
BACKGROUND_PLATFORM_WEIGHTS = [0.45, 0.30, 0.25]
# (log-median likes, sigma, comments ratio, shares ratio), as in v1
ENGAGEMENT = {
    "reddit": (4.0, 1.2, (0.10, 0.60), (0.00, 0.03)),
    "x": (4.5, 1.5, (0.02, 0.08), (0.05, 0.20)),
    "instagram": (5.5, 1.3, (0.01, 0.05), (0.005, 0.03)),
}
QUESTION_RE = re.compile(r"^(is|are|do|does|has|have|can|any|anyone|if)\b|\b(how|what|which|why|should|"
                         r"who|where|when|do you|does anyone|any good)\b", re.IGNORECASE)
TAIL_PERSONAS = {"everyday", "shopper", "marketer", "smallbiz", "creator"}  # people who add 'this morning' etc.
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


class Rng:
    """Built only on random.Random.random(), which is stable across Python
    versions (see v1); everything else is reimplemented on top of it."""

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

    def weighted_index(self, cum):
        return bisect.bisect_right(cum, self.random() * cum[-1])

    def lognormal(self, mu, sigma):
        u1, u2 = self.random() or 1e-12, self.random()
        return math.exp(mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2))


def cumulative(weights):
    out, total = [], 0.0
    for w in weights:
        total += w
        out.append(total)
    return out


class Zipf:
    """Draws items with weight 1/rank^s: a few common, a long rare tail."""

    def __init__(self, items, s=1.05):
        self.items = list(items)
        self.cum = cumulative([1.0 / (r + 1) ** s for r in range(len(self.items))])

    def draw(self, rng):
        return self.items[rng.weighted_index(self.cum)]


class Deck:
    """Every item once, shuffled, before any repeats (as in v1)."""

    def __init__(self, rng, items):
        self.rng, self.items, self.order = rng, list(items), []

    def draw(self):
        if not self.order:
            self.order = self.rng.sample(self.items, len(self.items))
        return self.order.pop()


# --------------------------------------------------------------------------
# Time: day/night curve, quieter weekends, trend curves
# --------------------------------------------------------------------------

def diurnal(hour_of_day):
    return 0.35 + 0.65 * (0.5 + 0.5 * math.cos(2 * math.pi * (hour_of_day - 21) / 24.0))


def activity(h):
    t = START + timedelta(hours=h)
    return diurnal(t.hour) * (0.8 if t.weekday() >= 5 else 1.0)


def rise(t0, k, floor=0.02):
    return lambda x: floor + 1.0 / (1.0 + math.exp(-(x - t0) / k))


def spike(t0, s, floor=0.01):
    return lambda x: floor + math.exp(-0.5 * ((x - t0) / s) ** 2)


def waves(t1, t2, s, floor=0.03):
    return lambda x: floor + math.exp(-0.5 * ((x - t1) / s) ** 2) + 0.8 * math.exp(-0.5 * ((x - t2) / s) ** 2)


def decline(tau, floor=0.04):
    return lambda x: floor + math.exp(-max(x, 0.0) / tau)


def steady(level=0.6):
    return lambda x: level


TRENDS = [  # share of trend posts; platforms: {platform: (share, lag hours)}; times in a 5-day frame
    {"id": "lowercase_brand_voice", "category": "social", "share": 0.22, "curve": ("spike", 93, 4),
     "platforms": {"x": (0.45, 0), "instagram": (0.35, 3), "reddit": (0.20, 10)}, "lowercase": 0.7},
    {"id": "dupe_culture", "category": "ecommerce", "share": 0.15, "curve": ("rise", 60, 16),
     "platforms": {"instagram": (0.40, 0), "reddit": (0.35, 4), "x": (0.25, 6)}},
    {"id": "quiet_luxury_fatigue", "category": "content", "share": 0.09, "curve": ("decline", 36),
     "platforms": {"instagram": (0.45, 0), "x": (0.35, 0), "reddit": (0.20, 0)}},
    {"id": "numbers_subject_lines", "category": "email", "share": 0.13, "curve": ("rise", 40, 6),
     "platforms": {"x": (0.45, 0), "reddit": (0.40, 6), "instagram": (0.15, 12)}},
    {"id": "founder_voice_cold_opens", "category": "email", "share": 0.11, "curve": ("waves", 28, 76, 6),
     "platforms": {"reddit": (0.40, 0), "x": (0.40, 3), "instagram": (0.20, 8)}},
    {"id": "pov_product_demos", "category": "social", "share": 0.10, "curve": ("steady",),
     "platforms": {"instagram": (0.55, 0), "x": (0.30, 0), "reddit": (0.15, 0)}},
    {"id": "girl_dinner_microcontent", "category": "content", "share": 0.10, "curve": ("rise", 70, 10),
     "platforms": {"instagram": (0.40, 0), "x": (0.35, 4), "reddit": (0.25, 10)}},
    {"id": "ai_ugc_disclosure", "category": "social", "share": 0.10, "curve": ("rise", 104, 5),
     "platforms": {"x": (0.45, 0), "reddit": (0.35, 2), "instagram": (0.20, 6)}},
]
AMPLIFIED = {"id": "giveaway_amplified", "category": "social", "curve": ("spike", 45, 2),
             "platforms": {"x": (0.8, 0), "instagram": (0.2, 0)}, "accounts": 5}


def build_curve(spec, scale):
    kind = spec[0]
    if kind == "rise":
        return rise(spec[1] * scale, spec[2] * scale)
    if kind == "spike":
        return spike(spec[1] * scale, spec[2] * scale)
    if kind == "waves":
        return waves(spec[1] * scale, spec[2] * scale, spec[3] * scale)
    if kind == "decline":
        return decline(spec[1] * scale)
    return steady()


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def words_key(text):
    """What te-normalizer's dedup compares: URLs gone, lowercase, no hashtags,
    mentions, emoji or punctuation. Independent posts must differ here."""
    key = _URL.sub(" ", text.lower())
    key = re.sub(r"[#@]\w+", " ", key)
    key = re.sub(r"[^\w\s$%]", " ", key)
    return " ".join(key.split())


def slot_values(rng):
    vals = {k: rng.choice(v) for k, v in T.SLOT_VALUES.items()}
    vals.update(p_lo=rng.randint(6, 22), p_hi=rng.randint(38, 120), pct=rng.randint(12, 48),
                pct2=rng.randint(8, 31), n=rng.randint(2, 9), k=rng.randint(2, 40), num=rng.randint(28, 95),
                mins=rng.randint(5, 90), month_word=rng.choice(MONTHS), nth=ordinal(rng.randint(2, 12)))
    return vals


def ordinal(n):
    return f"{n}" + ("th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def fill(rng, template):
    return template.format_map(slot_values(rng))


def typo(rng, text):
    words = text.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    if not idx:
        return text
    i = rng.choice(idx)
    w = words[i]
    j = rng.randint(1, len(w) - 2)
    words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def joined(text, phrase):
    """Adds a closing phrase as its own sentence, or an emoji as a trail."""
    if not phrase:
        return text
    if ord(phrase[0]) > 0x2000:
        return text + " " + phrase
    sep = " " if text.rstrip()[-1:] in ".?!" else ". "
    return text.rstrip() + sep + phrase


def voiced(rng, template, persona, tails, lowercase_p=0.0, boost=0):
    """boost (0-3) is raised by unique_core on retries, so a reused idea first
    tries the natural variations (opener, closer, a time or place) before
    giving up and becoming a verbatim repeat."""
    style = T.PERSONA_STYLE[persona]
    text = fill(rng, template)
    if (tails and persona in TAIL_PERSONAS and "?" not in text and not QUESTION_RE.search(text)
            and rng.chance(0.35 + 0.2 * boost)):
        text = text + " " + fill(rng, rng.choice(tails))
    plain = text[:1].islower() and "?" not in text and not QUESTION_RE.search(text) and not text.startswith("POV")
    question = "?" in text or bool(QUESTION_RE.search(text))
    if question and persona not in ("press", "brand"):
        if rng.chance(0.3 + 0.2 * boost):
            text = fill(rng, rng.choice(T.QUESTION_OPENERS)) + text
        if rng.chance(0.3 + 0.2 * boost):
            text = joined(text, rng.choice(T.QUESTION_CLOSERS))
    else:
        if plain and rng.chance(0.25 + 0.2 * boost):
            text = rng.choice(style["openers"]) + text
        if rng.chance(0.25 + 0.2 * boost):
            text = joined(text, rng.choice(style["closers"]))
    if persona != "press" and rng.chance(max(style["lower"], lowercase_p)):
        text = text.lower()
    if rng.chance(style["emoji"] * 0.6):
        text = joined(text, rng.choice(T.EMOJI))
    return text


# --------------------------------------------------------------------------
# Accounts, ids, urls, engagement
# --------------------------------------------------------------------------

HANDLE_A = ["growth", "copy", "brand", "social", "email", "content", "beauty", "budget", "thrifty", "swatch",
            "unbox", "saas", "smallbiz", "creative", "daily", "just", "notso", "the", "marketing", "retail",
            "shop", "indie", "caffeinated", "late", "honest", "tiny", "sunny", "quiet", "wild", "city",
            "plant", "coffee", "book", "pixel", "north", "salt", "maple", "river", "lucky", "cozy", "urban",
            "rainy", "golden", "velvet", "paper", "local", "midnight", "weekend"]
HANDLE_B = ["megs", "gal", "guy", "queen", "nerd", "lab", "club", "diaries", "hq", "notes", "sam", "alex",
            "jordan", "writes", "talks", "finds", "maven", "desk", "files", "studio", "collective", "chronicles",
            "mode", "works", "kitchen", "corner", "life", "dad", "mom", "girl", "runner", "baker", "reads",
            "eats", "travels", "fox", "owl", "bee", "cat", "dog"]
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def make_handle(rng, platform):
    sep = rng.choice(["", "", "_"] if platform == "instagram" else ["", "_", "-"] if platform == "reddit" else [""])
    name = rng.choice(HANDLE_A) + sep + rng.choice(HANDLE_B)
    if rng.chance(0.55):
        name += str(rng.randint(1, 999))
    return ("u/" if platform == "reddit" else "@") + name


def make_content_id(rng, platform):
    if platform == "reddit":
        return "t3_" + "".join(rng.choice(B36) for _ in range(7))
    if platform == "instagram":
        return "".join(rng.choice(B64URL) for _ in range(11))
    return "18" + "".join(str(rng.randint(0, 9)) for _ in range(17))


def make_url(platform, cid, author, subreddit):
    if platform == "reddit":
        return f"https://www.reddit.com/r/{subreddit}/comments/{cid[3:]}/"
    if platform == "x":
        return f"https://x.com/{author[1:]}/status/{cid}"
    return f"https://www.instagram.com/p/{cid}/"


def engagement(rng, platform, multiplier):
    mu, sigma, (c_lo, c_hi), (s_lo, s_hi) = ENGAGEMENT[platform]
    likes = max(0, int(rng.lognormal(mu, sigma) * multiplier))
    return {"likes": likes, "shares": int(likes * rng.uniform(s_lo, s_hi)),
            "comments": int(likes * rng.uniform(c_lo, c_hi))}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def generate(days, per_day, seed):
    rng = Rng(seed)
    hours = int(days * 24)
    scale = hours / REF_H
    total = int(per_day * days)

    authors = {p: Zipf([make_handle(rng, p) for _ in range(6000)], s=0.9) for p in PLATFORMS}
    spam_accounts = {p: [make_handle(rng, p) for _ in range(20)] for p in PLATFORMS}
    amp_accounts = [make_handle(rng, "x") for _ in range(AMPLIFIED["accounts"])]
    compound = [f"#{a}{b}" for a in T.TAG_PREFIX for b in T.TAG_SUFFIX]
    compound = rng.sample(compound, len(compound))
    marketing_tags = Zipf(T.MARKETING_TAGS, s=1.0)
    general_tags = Zipf(rng.sample(T.LIFESTYLE_TAGS, len(T.LIFESTYLE_TAGS)) + compound, s=1.05)

    act = [activity(h) for h in range(hours)]
    act_cum = cumulative(act)
    plat_cum = cumulative(BACKGROUND_PLATFORM_WEIGHTS)
    seen, used_ids, records = {}, set(), []   # seen: dedup key -> first contentId

    def new_id(platform):
        while True:
            cid = make_content_id(rng, platform)
            if cid not in used_ids:
                used_ids.add(cid)
                return cid

    def background_tags(platform, group, own=()):
        """Hashtags for a post: its own (trend/topic) plus long-tail background ones."""
        pool = marketing_tags if group in ("marketing", "smallbiz", "shopper") else general_tags
        if platform == "reddit":
            n_bg = 1 if rng.chance(0.05) else 0
            own = own[:1] if rng.chance(0.1) else []
        elif platform == "x":
            n_bg = rng.choice([0, 0, 0, 1, 1, 2])
        else:
            n_bg = rng.randint(1, 5)
        tags = list(own) + [pool.draw(rng) for _ in range(n_bg)]
        return list(dict.fromkeys(t for t in tags))

    def render(platform, core, group, tags, author):
        """Platform conventions around a core text."""
        if platform == "reddit":
            title = core[:1].upper() + core[1:] if core[:1].isalpha() and not core.islower() else core
            body_group = group if group in T.REDDIT_BODY else "marketing"
            lines = [fill(rng, rng.choice(T.REDDIT_BODY[body_group])) for _ in range(rng.choice([0, 1, 1, 2]))]
            text = title + ("\n\n" + " ".join(dict.fromkeys(lines)) if lines else "")
            return text + ("\n\n" + " ".join(tags) if tags else "")
        if platform == "x":
            text = core
            if rng.chance(0.08):
                text = authors["x"].draw(rng) + " " + text      # a reply
            if rng.chance(0.05):
                text += " https://bit.ly/" + "".join(rng.choice(B64URL[:62]) for _ in range(7))
            return text + (" " + " ".join(tags) if tags else "")
        return core + ("\n\n" + " ".join(tags) if tags else "")

    def emit(platform, when, core, group, tags, label, multiplier, author=None, raw=None):
        author = author or authors[platform].draw(rng)
        sub = rng.choice(T.SUBREDDITS[group if group in T.SUBREDDITS else "marketing"]) if platform == "reddit" else None
        cid = new_id(platform)
        text = raw if raw is not None else render(platform, core, group, tags, author)
        rec = {"sourceId": SOURCE_ID[platform], "sourceType": "api", "contentId": cid, "rawText": text,
               "author": author, "postedAt": iso(when), "engagement": engagement(rng, platform, multiplier),
               "url": make_url(platform, cid, author, sub)}
        records.append((rec, label, core, group, tags, platform))
        k = words_key(core)
        if seen.get(k, "x") is None:
            seen[k] = cid
        return rec

    def maybe_typo(core, persona):
        """Typos are added after the text is unique, so they never decide uniqueness."""
        if persona in T.PERSONA_STYLE and rng.chance(T.PERSONA_STYLE[persona]["typo"]):
            t = typo(rng, core)
            if words_key(t) not in seen:
                seen.pop(words_key(core), None)
                seen[words_key(t)] = None
                return t
        return core

    def unique_core(make, repeats_ok=False):
        """Builds a core text whose dedup key is new, so independent people
        never collide into what dedup would call a repost. Returns
        (core, contentId of an earlier post with the same words or None).
        When the variations run out the post becomes a verbatim repeat of the
        earlier one and is labelled so (dedup will drop it, as it would a real
        copy-paste). repeats_ok is kept for readability at call sites where
        repeats are the norm (spam, bots, press headlines)."""
        for attempt in range(40):
            core = make(attempt)
            key = words_key(core)
            if key and key not in seen:
                seen[key] = None          # filled with the contentId once the post is emitted
                return core, None
        return core, seen.get(key)   # out of variations: a verbatim repeat, labelled as one

    def at(hour_idx):
        return START + timedelta(hours=hour_idx, seconds=rng.randint(0, 3599))

    counts = {k: round(total * v) for k, v in MIX.items()}

    # Everyday noise, by topic
    topics = list(T.NOISE_TOPICS)
    topic_cum = cumulative([len(T.NOISE_TOPICS[t]["texts"]) for t in topics])
    decks = {t: Deck(rng, T.NOISE_TOPICS[t]["texts"]) for t in topics}
    for _ in range(counts["noise"]):
        topic = topics[rng.weighted_index(topic_cum)]
        tmpl = decks[topic].draw()
        core, dup_of = unique_core(lambda a: voiced(rng, tmpl, "everyday", T.NOISE_TAILS, boost=a // 10))
        core = core if dup_of else maybe_typo(core, "everyday")
        p = PLATFORMS[rng.weighted_index(plat_cum)]
        own = rng.sample(T.NOISE_TOPICS[topic]["tags"], rng.randint(0, 2))
        label = {"kind": "noise", "subkind": "everyday", "persona": "everyday"}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(act_cum)), core, "noise", background_tags(p, "noise", own), label, 0.3)

    # Evergreen marketing talk
    deck = Deck(rng, T.EVERGREEN_TEXTS)
    for _ in range(counts["evergreen"]):
        persona, tmpl = deck.draw()
        core, dup_of = unique_core(lambda a: voiced(rng, tmpl, persona, T.MARKETING_TAILS, boost=a // 10),
                                   repeats_ok=persona in ("press", "brand"))
        core = core if dup_of else maybe_typo(core, persona)
        p = PLATFORMS[rng.weighted_index(plat_cum)]
        group = "smallbiz" if persona == "smallbiz" else "marketing"
        label = {"kind": "evergreen", "subkind": "evergreen", "persona": persona}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(act_cum)), core, group, background_tags(p, group), label, 0.6)

    # Traps: look-alikes, spam, non-English
    deck = Deck(rng, T.LOOKALIKE_TEXTS)
    for _ in range(counts["lookalike"]):
        tmpl = deck.draw()
        core, dup_of = unique_core(lambda a: voiced(rng, tmpl, "everyday", T.NOISE_TAILS, boost=a // 10))
        p = PLATFORMS[rng.weighted_index(plat_cum)]
        label = {"kind": "noise", "subkind": "lookalike", "persona": "everyday"}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(act_cum)), core, "noise", background_tags(p, "noise"), label, 0.3)
    deck = Deck(rng, T.SPAM_TEXTS)
    for _ in range(counts["spam"]):
        tmpl = deck.draw()
        core, dup_of = unique_core(lambda a: fill(rng, tmpl), repeats_ok=True)
        p = rng.choice(["x", "x", "instagram"])
        tags = [marketing_tags.draw(rng) for _ in range(rng.randint(1, 4))]
        label = {"kind": "noise", "subkind": "spam", "persona": "spam"}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(act_cum)), core, "noise", list(dict.fromkeys(tags)), label, 0.05,
             author=rng.choice(spam_accounts[p]))
    deck = Deck(rng, T.FOREIGN_TEXTS)
    for _ in range(counts["foreign"]):
        lang, tmpl = deck.draw()
        core, dup_of = unique_core(lambda a: fill(rng, tmpl), repeats_ok=True)
        p = PLATFORMS[rng.weighted_index(plat_cum)]
        label = {"kind": "noise", "subkind": "foreign", "persona": "everyday", "lang": lang}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(act_cum)), core, "noise", background_tags(p, "noise"), label, 0.4)

    # Planted trends
    for trend in TRENDS:
        n = round(counts["trend"] * trend["share"])
        curve = build_curve(trend["curve"], scale)
        plats = list(trend["platforms"])
        pcum = cumulative([trend["platforms"][p][0] for p in plats])
        hour_cum, peak = {}, {}
        for p in plats:
            lag = trend["platforms"][p][1] * scale
            hour_cum[p] = cumulative([curve(h - lag) * act[h] for h in range(hours)])
            peak[p] = max(curve(h - lag) for h in range(hours))
        deck = Deck(rng, T.TREND_TEXTS[trend["id"]])
        own_tags = T.TREND_HASHTAGS[trend["id"]]
        for _ in range(n):
            persona, tmpl = deck.draw()
            core, dup_of = unique_core(lambda a: voiced(rng, tmpl, persona, T.MARKETING_TAILS, trend.get("lowercase", 0.0), a // 10),
                                       repeats_ok=persona in ("press", "brand"))
            core = core if dup_of else maybe_typo(core, persona)
            p = plats[rng.weighted_index(pcum)]
            h = rng.weighted_index(hour_cum[p])
            intensity = curve(h - trend["platforms"][p][1] * scale) / peak[p]
            own = ([own_tags[0]] + rng.sample(own_tags[1:], rng.choice([0, 1]))) if own_tags else []
            group = "shopper" if persona == "shopper" else "smallbiz" if persona == "smallbiz" else "marketing"
            label = {"kind": "trend", "trend": trend["id"], "subkind": "trend", "persona": persona}
            if dup_of:
                label.update(duplicateOf=dup_of, duplicateKind="repeat")
            emit(p, at(h), core, group, background_tags(p, group, own), label, 0.8 + 2.5 * intensity)

    # A bot-amplified giveaway: lots of posts, a handful of accounts
    curve = build_curve(AMPLIFIED["curve"], scale)
    plats = list(AMPLIFIED["platforms"])
    pcum = cumulative([AMPLIFIED["platforms"][p][0] for p in plats])
    hcum = cumulative([curve(h) * act[h] for h in range(hours)])
    deck = Deck(rng, T.TREND_TEXTS[AMPLIFIED["id"]])
    for _ in range(counts["amplified"]):
        persona, tmpl = deck.draw()
        core, dup_of = unique_core(lambda a: fill(rng, tmpl), repeats_ok=True)
        p = plats[rng.weighted_index(pcum)]
        own = ["#megagiveaway"] + rng.sample(["#giveaway", "#win"], 1)
        label = {"kind": "evergreen", "trend": AMPLIFIED["id"], "subkind": "amplified", "persona": "brand"}
        if dup_of:
            label.update(duplicateOf=dup_of, duplicateKind="repeat")
        emit(p, at(rng.weighted_index(hcum)), core, "marketing", own, label, 0.1,
             author=rng.choice(amp_accounts) if p == "x" else None)

    # Duplicates: exact reposts (same text, same platform) and near-duplicate
    # cross-posts (same words, re-tagged for another platform; X/Instagram only,
    # since a Reddit body would change the words).
    end = START + timedelta(hours=hours)
    pool = [r for r in records if r[1].get("subkind") in ("trend", "evergreen") and not r[1].get("duplicateOf")]
    for kind, share in (("exact", 0.012), ("near", 0.015)):
        candidates = pool if kind == "exact" else [r for r in pool if r[5] in ("x", "instagram")]
        for orig in rng.sample(candidates, round(total * share)):
            rec, label, core, group, tags, platform = orig
            when = min(datetime.strptime(rec["postedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                       + timedelta(minutes=rng.randint(5, 600)), end - timedelta(seconds=1))
            dup_label = dict(label, duplicateOf=rec["contentId"], duplicateKind=kind)
            if kind == "exact":
                emit(platform, when, core, group, tags, dup_label, 0.4, raw=rec["rawText"])
            else:
                other = "instagram" if platform == "x" else "x"
                emit(other, when, core, group, background_tags(other, group, tags[:2]), dup_label, 0.7)

    records.sort(key=lambda r: (r[0]["postedAt"], r[0]["contentId"]))
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=5, help="span in days, ending at the start of the current UTC hour (default 5)")
    ap.add_argument("--per-day", type=int, default=8000, help="base posts per day before duplicates (default 8000)")
    ap.add_argument("--seed", type=int, default=20260907)
    ap.add_argument("--out", default="fixtures/raw_content_v2.jsonl")
    ap.add_argument("--labels", default="fixtures/raw_content_v2.labels.jsonl")
    args = ap.parse_args(argv)

    global START
    START = start_for(args.days)
    records = generate(args.days, args.per_day, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec, *_ in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(args.labels, "w", encoding="utf-8") as f:
        for rec, label, *_ in records:
            f.write(json.dumps({
                "contentId": rec["contentId"], "sourceId": rec["sourceId"], "postedAt": rec["postedAt"],
                "kind": label["kind"], "trend": label.get("trend"), "subkind": label.get("subkind"),
                "persona": label.get("persona"), "lang": label.get("lang", "en"),
                "duplicateOf": label.get("duplicateOf"), "duplicateKind": label.get("duplicateKind"),
            }, ensure_ascii=False) + "\n")

    by = {}
    for rec, label, *_ in records:
        key = label.get("trend") or label.get("subkind") or label["kind"]
        if label.get("duplicateKind"):
            key = "duplicate:" + label["duplicateKind"]
        plat = next(p for p in PLATFORMS if SOURCE_ID[p] == rec["sourceId"])
        by.setdefault(key, {}).setdefault(plat, 0)
        by[key][plat] += 1
    print(f"wrote {len(records)} records to {args.out} (+ labels to {args.labels})", file=sys.stderr)
    print(f"{'':28}" + "".join(f"{p:>11}" for p in PLATFORMS) + f"{'total':>8}", file=sys.stderr)
    for key in sorted(by):
        row = by[key]
        print(f"{key:28}" + "".join(f"{row.get(p, 0):>11}" for p in PLATFORMS) + f"{sum(row.values()):>8}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
