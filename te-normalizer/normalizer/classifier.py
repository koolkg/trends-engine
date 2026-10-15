"""NORM-4: relevance + taxonomy classifier — a keyword/rule table: a rule-based
stand-in is the honest default, and a trained classifier is out of scope for
the PoC.

Each category has weighted keywords and phrases. A post's score for a category
is the sum of the weights that match. Hashtags count as their bare word, so
"#emailmarketing" matches "emailmarketing". relevance_score is the total of
all matched weights (every category), scaled into [0, 1] (SATURATION points =
1.0); the category is the one with the highest total; the post is relevant if
the score clears the configured threshold. Categories match the dashboard's
filters: email, social, ecommerce, content.

Since NORM-9 this table is the backup and validator for the model in
semantic.py (see relevance.py), not the only classifier.
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional

CATEGORIES = ("email", "social", "ecommerce", "content")
SATURATION = 4.0  # total weight that maps to relevance_score 1.0

# General marketing and shopping vocabulary, not phrases from the fixture's
# posts. Catchphrases that also appear in everyday off-topic posts ("girl
# dinner" about food, "POV" pet videos) carry little weight on their own: they
# only clear the threshold together with real marketing words.
KEYWORDS: Dict[str, Dict[str, float]] = {
    "email": {
        "email": 2, "emails": 2, "emailmarketing": 3, "emailmarketingtips": 3, "newsletter": 2,
        "newsletters": 2, "inbox": 2, "subject line": 3, "subject lines": 3, "subjectlines": 3,
        "open rate": 3, "open rates": 3, "opens": 2, "opener": 1, "openers": 1, "cold open": 2, "preheader": 3,
        "unsubscribe": 2, "unsubscribes": 2, "unsubscribed": 2, "subscribers": 2, "subscriber": 2,
        "welcome series": 3, "welcome email": 3, "abandoned cart": 3, "sends": 1, "click rate": 3,
        "reply rate": 2, "deliverability": 3, "inbox placement": 3, "promo email": 2, "plain-text": 1,
        "plain text": 1, "a/b": 2, "a b test": 2, "split-tested": 2, "ctr": 2, "clicks": 1, "segmenting": 2,
        "list": 0.5, "replies": 1, "founder": 1, "founderstory": 2, "intro": 2, "intros": 2, "deals": 1,
        "hours left": 2, "savings": 1, "sms": 1, "founders": 1,
    },
    "social": {
        "social": 2, "socialmedia": 2, "socialmediatips": 2, "post": 1, "posts": 1, "posting": 1,
        "caption": 2, "captions": 2, "feed": 1, "followers": 2, "follows": 1, "engagement": 2, "reels": 2,
        "reel": 2, "pov": 1, "ugc": 3, "brand voice": 3, "brandvoice": 3, "brand account": 3, "lowercase": 1,
        "hashtag": 2, "views": 1, "impressions": 2, "watch-through": 3, "watch through": 3, "reach": 2,
        "algorithm": 2, "likes": 1, "comments": 1, "mentions": 1, "bio": 1, "stories": 1, "account": 1,
        "page": 1, "tiktok": 1, "instagram": 1, "pinterest": 1, "linkedin": 1, "save rate": 2,
        "completion rate": 2, "retention": 1, "video": 1, "videos": 1, "unboxing": 2, "productdemo": 2,
        "demo": 1, "demos": 1, "comment section": 2, "scheduling tool": 2, "organic social": 3,
        "influencer": 2, "creators": 1, "giveaway": 2, "kpis": 2, "saves": 1, "ads": 1, "ad": 1, "cpa": 2,
        "agency": 1, "audience": 1, "first person": 1, "first-person": 1, "asking for a client": 2, "alt text": 1,
    },
    "ecommerce": {
        "dupe": 3, "dupes": 3, "dupealert": 3, "budgetbeauty": 2, "affordablemakeup": 2, "knockoff": 3,
        "knockoffs": 3, "lookalikes": 2, "alternative": 1, "drugstore": 2, "full price": 2, "on sale": 1,
        "sold out": 1, "restocked": 2, "back in stock": 2, "waitlist": 1, "haul": 2, "price": 1,
        "packaging": 1, "expensive": 1, "cheap": 1, "cheaper": 1, "ordered": 1, "bought": 0.5,
        "product": 2, "products": 1, "product descriptions": 3, "product page": 2, "product pages": 2,
        "hero product": 3, "customers": 2, "customer": 2, "store": 1, "shop": 1, "retailer": 2,
        "retailers": 2, "sale": 1, "sales": 1, "checkout": 2, "cart": 2, "outsell": 2, "revenue": 2,
        "conversion": 2, "conversion rate": 2, "landing page": 2, "free shipping": 3, "shopify": 2,
        "tiktok shop": 2, "referral program": 2, "black friday": 2, "reviews": 1, "swatch": 1, "swatches": 1,
        "ecommerce": 3, "name brand": 2, "original": 1, "buyer": 1, "buyers": 1,
        # retailers and everyday product types people name when they talk about buying things
        "amazon": 1, "walmart": 1, "target": 1, "aldi": 1, "tj maxx": 1, "trader joe's": 1,
        "serum": 1, "primer": 1, "setting spray": 1, "lip oil": 1, "foundation": 1, "concealer": 1,
        "moisturizer": 1, "perfume": 1, "blush": 1, "bronzer": 1, "eye cream": 1, "spf": 1,
        "skincare": 1, "makeup": 1, "beauty": 1,
    },
    "content": {
        "content": 2, "contentcreator": 2, "creator": 1, "creatoreconomy": 2, "editorial": 2,
        "calendar": 1, "carousel": 2, "carousels": 2, "copy": 2, "copywriter": 2, "copywriters": 2,
        "copywriting": 2, "messaging": 2, "campaign": 2, "aesthetic": 2, "format": 2, "micro-content": 3,
        "quiet luxury": 3, "quietluxury": 3, "old money": 3, "oldmoney": 3, "stealth wealth": 2,
        "minimalist": 1, "understated": 1, "neutral": 1, "neutrals": 1, "beige": 1, "linen": 1,
        "lookbook": 2, "moodboard": 2, "rebrand": 2, "palette": 1, "creative director": 2,
        "style guide": 2, "brand guidelines": 2, "about us": 1, "brand": 1, "brands": 1, "blog": 2,
        "marketing": 2, "marketer": 2, "marketers": 2, "roundup": 2, "roundups": 2, "strategy": 1, "b2b": 2, "q4": 2, "repurposing": 2,
        "performing": 1, "performer": 1, "roi": 2, "polish": 1, "polished": 1, "unpolished": 1,
        "low effort": 2, "low-effort": 2, "production value": 2, "behind the scenes": 2, "listicle": 2,
        "photo dump": 2, "photo dumps": 2, "designer": 1, "designers": 1, "scraps": 1, "girl dinner": 1,
        "snack-size": 2, "snack-sized": 2, "snack plate": 2, "bits and pieces": 2, "bits-and-pieces": 2,
        "odds and ends": 1, "specificity": 1, "competitors": 1,
    },
}
PRICE = re.compile(r"\$\d+")  # a price is a (weak) ecommerce signal
PRICE_WEIGHT = 1.0
PERCENT_OFF = re.compile(r"\d+% off|save \d+%")  # promo copy: an email/offer signal
PERCENT_OFF_WEIGHT = 1.0

_PATTERNS = {
    cat: [(re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)"), w) for kw, w in table.items()]
    for cat, table in KEYWORDS.items()
}


@dataclass(frozen=True)
class Classification:
    category: Optional[str]
    relevance_score: float  # 0.000 - 1.000 (matches NUMERIC(4,3))
    raw_scores: Dict[str, float]


def classify(cleaned: str) -> Classification:
    text = cleaned.lower().replace("#", " ")
    scores = {cat: sum(w for pat, w in _PATTERNS[cat] if pat.search(text)) for cat in CATEGORIES}
    if PRICE.search(text):
        scores["ecommerce"] += PRICE_WEIGHT
    if PERCENT_OFF.search(text):
        scores["email"] += PERCENT_OFF_WEIGHT
    total = sum(scores.values())
    if total == 0:
        return Classification(None, 0.0, scores)
    # Best category; ties break by CATEGORIES order.
    category = max(CATEGORIES, key=lambda c: (scores[c], -CATEGORIES.index(c)))
    return Classification(category, round(min(1.0, total / SATURATION), 3), scores)


def is_relevant(result: Classification, threshold: float) -> bool:
    return result.category is not None and result.relevance_score >= threshold
