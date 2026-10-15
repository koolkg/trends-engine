"""AGG-4 (decision AGG-8): trend name + summary + detail from a template, no LLM.

Deterministic, so tests can assert exact output, and no API key is needed.
Everything te-api serves for a trend is assembled here: list fields (name,
category, velocityScore, deltaPct, firstSeenAt, sources, sparkline) and
detail fields (relatedTerms, samplePosts, growth, clusterId). A future LLM
synthesizer only has to replace `name` and `summary`.
"""
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aggregator.ranking import HASHTAG, Trend

NAMESPACE = uuid.UUID("6a1f6d2e-3c4b-4f7a-9e2d-7b1c0a9f5e31")  # fixed: same key -> same trend id every cycle
GENERIC_TAGS = {"fyp", "foryou", "foryoupage", "viral", "trending", "marketing", "smallbusiness", "contentcreator",
                "socialmediatips", "marketingtips", "entrepreneur", "digitalmarketing", "creatoreconomy", "ugc",
                "beauty", "makeup", "skincare", "life", "weekend", "mood", "love", "instagood"}
STOPWORDS = set("""a an the and or but if so to of in on for with at by from is are was were be been it its this that
these those i im me my we our you your they their he she just not no yes do does did have has had what who why how when
all any some more most very really about into over than then too can will would could should get got also like one two
up out new now still here there day week weeks ok okay honestly anyway because again ever even only every much
it's i'm don't can't that's what's let's we're they're you're isn't""".split())
SPIKE = "spike"


def trend_id(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, key))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _words(text: str) -> List[str]:
    text = HASHTAG.sub(" ", text.lower())
    return [w for w in re.findall(r"[a-z][a-z'\-]+", text) if w not in STOPWORDS and len(w) > 2]


# Everyday marketing words. A phrase made of these ("content strategy", "open
# rate") describes the conversation, not the trend, so it loses ties to a
# phrase with a distinctive word in it ("girl dinner").
GENERIC_WORDS = set("""content strategy marketing marketer email emails newsletter inbox post posts posting brand
brands social media engagement campaign copy open opens rate rates subject line lines product products video
videos customer customers audience sales account page feed test tested results best performing performs
performer week month today year team""".split())


def _generic_count(phrase: str) -> int:
    return sum(w in GENERIC_WORDS for w in phrase.split())


def top_phrases(texts: List[str], k: int = 3, min_count: int = 3) -> List[str]:
    """Most repeated two-word phrases (counted once per post). Ties go to the
    phrase with fewer generic marketing words, then alphabetically (deterministic)."""
    bigrams = Counter()
    for t in texts:
        w = _words(t)
        bigrams.update({f"{a} {b}" for a, b in zip(w, w[1:])})  # once per post
    ranked = sorted(bigrams.items(), key=lambda pn: (-pn[1], _generic_count(pn[0]), pn[0]))
    return [p for p, n in ranked[:k] if n >= min_count]


def top_tags(texts: List[str], exclude: Optional[str], k: int = 3) -> List[str]:
    ex = (exclude or "").lower().lstrip("#")
    tags = Counter()
    for t in texts:
        tags.update({m.lower() for m in HASHTAG.findall(t)} - GENERIC_TAGS - {ex})
    ranked = sorted(tags.items(), key=lambda tn: (-tn[1], tn[0]))
    return ["#" + t for t, n in ranked[:k] if n >= 2]


def hourly_counts(posts, end: datetime, hours: int) -> List[Dict]:
    start = end - timedelta(hours=hours)
    buckets = [0] * hours
    for p in posts:
        if start <= p.posted_at < end:
            buckets[int((p.posted_at - start) / timedelta(hours=1))] += 1
    return [{"date": _iso(start + timedelta(hours=i)), "score": n} for i, n in enumerate(buckets)]


def delta_pct(frequency: int, baseline: float) -> int:
    return int(round((frequency - baseline) / max(baseline, 1.0) * 100))


PLATFORM_NAMES = {"reddit": "Reddit", "x": "X", "instagram": "Instagram", "tiktok": "TikTok"}


def _hour(dt: datetime) -> str:
    """'7 PM UTC'"""
    return f"{(dt.hour % 12) or 12} {'AM' if dt.hour < 12 else 'PM'} UTC"


def _and(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def highlights(trend: Trend, growth: List[Dict], spiking: bool) -> List[str]:
    """Why it's trending, in plain words for a marketer: how big the jump is,
    whether it's still growing, where it spread, how many different people are
    behind it, and whether people discuss it beyond the hashtag. The z-scores
    behind the ranking stay in `signals` (API and logs), not on the dashboard."""
    out: List[str] = []
    lead, posts = trend.lead, trend.posts
    w = lead.best
    usual = "usually fewer than 1 an hour" if w.baseline < 1 else f"usually about {round(w.baseline)} an hour"
    who = lead.ref if lead.kind == "term" else "Posts about it"
    out.append(f"{who} jumped to {w.frequency} posts in the {_hour(w.start)} hour ({usual}).")

    counts = [g["score"] for g in growth]
    peak = max(counts) if counts else 0
    if peak:
        if spiking:
            out.append("Spiking right now.")
        elif counts[-1] >= 0.6 * peak:
            out.append("Still near its busiest in the latest hour.")
        else:
            peak_at = datetime.fromisoformat(growth[counts.index(peak)]["date"].replace("Z", "+00:00"))
            out.append(f"Cooling off since its peak around {_hour(peak_at)}.")

    first: Dict[str, datetime] = {}
    for p in sorted(posts, key=lambda p: p.posted_at):
        first.setdefault(p.source, p.posted_at)
    order = [PLATFORM_NAMES.get(s, s) for s in sorted(first, key=lambda s: first[s])]
    if len(order) == 1:
        out.append(f"Only on {order[0]} so far.")
    elif order:
        span = (max(first.values()) - min(first.values())) / timedelta(hours=1)
        hours = round(span)
        within = "within the same hour" if span < 1 else f"within {hours} hour{'s' if hours != 1 else ''}"
        out.append(f"Started on {order[0]}, then spread to {_and(order[1:])} {within}.")

    authors = {p.author for p in posts if p.author}
    if len(authors) >= 5:
        if len(authors) >= 0.6 * len(posts):
            out.append(f"{len(authors)} different accounts are posting about it, so it isn't one account pushing it.")
        else:
            out.append(f"{len(posts)} posts come from just {len(authors)} accounts, so a few accounts drive much of it.")

    if trend.terms and trend.clusters:
        tags = {t.ref.lower().lstrip("#") for t in trend.terms}
        own_words = [p for p in posts if not tags & {m.lower() for m in HASHTAG.findall(p.text)}]
        if len(own_words) >= 2:
            out.append(f"{len(own_words)} more posts talk about it in their own words, without the hashtag.")
    elif trend.clusters and not trend.terms:
        out.append("There's no shared hashtag: these posts were grouped because they say the same thing "
                   "in different words.")
    return out


def plain_summary(n_posts: int, hours: int, sources: List[str], delta: int) -> str:
    """'84 posts in the last 12 hours on X, TikTok and Instagram, about 11 times its usual pace.'"""
    where = f" on {_and([PLATFORM_NAMES.get(s, s) for s in sources])}" if sources else ""
    if delta >= 100:
        pace = f"about {round(1 + delta / 100)} times its usual pace"
    elif delta > 0:
        pace = "above its usual pace"
    elif delta < 0:
        pace = "below its usual pace"
    else:
        pace = "about its usual pace"
    return f"{n_posts} post{'s' if n_posts != 1 else ''} in the last {hours} hours{where}, {pace}."


def synthesize(trend: Trend, rank: int, now: datetime, sparkline_hours: int, spike_z: float) -> Dict:
    lead, posts = trend.lead, trend.posts
    texts = [p.text for p in posts]
    tags = [t.ref for t in trend.terms]
    tag = tags[0] if tags else None
    phrases = top_phrases(texts)
    if tags:  # hashtag trends are named by their hashtags: precise and stable
        name = " / ".join(tags[:2])
    elif phrases:  # hashtag-less (cluster-only) trend: its most repeated phrase
        name = f'"{phrases[0]}"'
    else:  # tiny cluster with no repeated phrase: lead with its most-engaged post
        best = max(posts, key=lambda p: p.engagement) if posts else None
        name = ('"' + " ".join(best.text.split()[:6]) + '…"') if best else trend.key
    categories = Counter(p.category for p in posts if p.category)
    sources = [s for s, _ in sorted(Counter(p.source for p in posts).items(), key=lambda sn: (-sn[1], sn[0]))]
    growth = hourly_counts(posts, now, sparkline_hours)
    spiking = lead.best.z >= spike_z and lead.hours_ago <= 2
    delta = delta_pct(lead.best.frequency, lead.best.baseline)
    window_posts = [p for p in posts if p.posted_at >= now - timedelta(hours=sparkline_hours)]
    summary = plain_summary(len(window_posts), sparkline_hours, sources, delta)
    related = [t for t in top_tags(texts, tag) if t not in name][:3] + [p for p in phrases if p not in name][:2]
    samples = sorted(posts, key=lambda p: (-p.engagement, p.posted_at))[:3]
    return {
        "trendId": trend_id(trend.key),
        "rank": rank,
        "name": name,
        "summary": summary,
        "category": sorted(categories.items(), key=lambda cn: (-cn[1], cn[0]))[0][0] if categories else "content",
        "velocityScore": round(trend.score, 2),
        "velocityLabel": SPIKE if spiking else "rising",
        "deltaPct": delta,
        "firstSeenAt": _iso(min(p.posted_at for p in posts)) if posts else None,
        "sources": sources,
        "sparkline": [g["score"] for g in growth],
        "clusterId": trend.cluster.ref if trend.cluster else None,
        "relatedTerms": related,
        "samplePosts": [{"text": p.text, "source": p.source, "postedAt": _iso(p.posted_at), "author": p.author}
                        for p in samples],
        "growth": growth,
        "highlights": highlights(trend, growth, spiking),
        "signals": [{"type": c.kind, "ref": c.ref, "zScore": c.best.z, "frequency": c.best.frequency,
                     "baseline": c.best.baseline, "windowStart": _iso(c.best.start)}
                    for c in trend.terms + trend.clusters],
        "signalKey": trend.key,
    }
