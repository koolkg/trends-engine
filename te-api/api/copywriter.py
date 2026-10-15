"""Suggested copy for a trend, from a template per tone (no LLM, same reason as
te-aggregator's AGG-8: deterministic, testable, no API key). A future LLM
copywriter only has to return the same Copy.

Built from what te-aggregator already says about the trend: its name, how
far above normal it is (deltaPct) and where it's happening (sources). It's
copy for the marketer's customers, so no hashtags, and sample posts are never
quoted: they're other people's words.
"""
import html
import re
from dataclasses import dataclass
from typing import Dict, List

TONES = ("casual", "professional", "expert")
ALIASES = {"playful": "casual"}  # the design doc's example value
PLATFORMS = {"reddit": "Reddit", "x": "X", "instagram": "Instagram", "tiktok": "TikTok"}


@dataclass(frozen=True)
class Copy:
    subject_line: str
    preview_text: str
    headline: str
    body: str            # the suggested block: what te-ui shows and campaign_draft_block stores


def topic(name: str) -> str:
    """'#lowercase / #brandvoice' -> 'lowercase'; '"girl dinner"' -> 'girl dinner'."""
    first = name.split(" / ")[0]
    return re.sub(r"[#\"“”…]", "", first).strip() or name


def _platforms(sources: List[str]) -> str:
    names = [PLATFORMS.get(s, s) for s in sources[:3]]
    if not names:
        return "social media"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def write(trend: Dict, tone: str) -> Copy:
    tone = ALIASES.get(tone, tone)
    if tone not in TONES:
        raise ValueError(f"tone must be one of {', '.join(TONES)}")
    t = topic(trend.get("name") or "this trend")
    big = t[:1].upper() + t[1:]
    where = _platforms(trend.get("sources") or [])
    delta = trend.get("deltaPct") or 0
    rise = f"{delta}% more than usual" if delta > 0 else "more every day"
    if tone == "casual":
        return Copy(f"{big} is having a moment ✨", f"Everyone's talking about {t}. Here's our take.",
                    f"Have you noticed {t}?",
                    f"{big} is everywhere right now, with people on {where} talking about it {rise}. "
                    f"We couldn't resist joining in, so here's our take, made just for you.")
    if tone == "professional":
        return Copy(f"What the {t} trend means for you", f"Conversation about {t} is up across {where}.",
                    f"The {t} trend, explained",
                    f"Conversation about {t} is growing fast on {where} ({rise}). "
                    f"Here's what we're seeing and how we're thinking about it.")
    # expert: numbers first, analytical, authoritative
    up = f"up {delta}% over their usual pace" if delta > 0 else "still climbing"
    return Copy(f"{big}, by the numbers", f"The numbers behind the {t} trend on {where}.",
                f"Inside the {t} trend",
                f"Posts about {t} are {up} on {where}. We dug into what's behind the jump "
                f"and what it means for you. Here's the short version.")


def to_html(c: Copy) -> str:
    """The block as email HTML: a headline and a paragraph. No button: the
    marketer adds their own call to action (and where it goes) in Mailchimp."""
    e = html.escape
    return f"<html><body><h1>{e(c.headline)}</h1><p>{e(c.body)}</p></body></html>"


def to_plain(c: Copy) -> str:
    return f"{c.headline}\n\n{c.body}"
