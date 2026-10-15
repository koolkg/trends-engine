"""NORM-3: duplicate detection by exact hash of a normalized dedup key
(decided in NORM-8; see README).

The key is the cleaned text lowercased, with hashtags, @mentions, emoji and
punctuation removed. So a repost or cross-post that differs only in hashtags,
emoji, casing or punctuation hashes the same, while two posts that merely
share wording (the echo that *is* a trend) don't. Uniqueness is enforced by
the unique index on normalized_content_item.dedup_hash; this module only
computes the hash. Swap this one function to change the approach.
"""
import hashlib
import re

_TAGS_MENTIONS = re.compile(r"[#@]\w+")
_NON_WORD = re.compile(r"[^\w\s$%]")  # keeps prices/percentages: "$12" vs "$58" stay distinct
_WS = re.compile(r"\s+")


def dedup_key(cleaned: str) -> str:
    key = cleaned.lower()
    key = _TAGS_MENTIONS.sub(" ", key)
    key = _NON_WORD.sub(" ", key)
    key = _WS.sub(" ", key).strip()
    # A post that's nothing but hashtags/emoji would otherwise reduce to "" and
    # collide with every other such post; fall back to the full lowercased text.
    return key or cleaned.lower().strip()


def dedup_hash(cleaned: str) -> str:
    return hashlib.sha256(dedup_key(cleaned).encode("utf-8")).hexdigest()
