"""DET-2, hashtag-only (design decision DET-0): the terms te-detector
tracks are hashtags. Free-text keywords split each trend into dozens of
fragments at this volume, and phrases and hashtag-less trends are
te-cluster's job."""
import re
from typing import FrozenSet, Set

_HASHTAG = re.compile(r"(?<![\w#])#(\w{2,})")


def extract_hashtags(text: str, ignore: FrozenSet[str] = frozenset()) -> Set[str]:
    """Distinct hashtags in `text`, lowercased with the '#' kept (e.g. "#lowercase").
    A post counts once per tag even if it repeats it. Tags in `ignore` (given
    without '#') are dropped, as are purely numeric ones (#1, #2026)."""
    tags = set()
    for raw in _HASHTAG.findall(text):
        tag = raw.lower()
        if tag in ignore or tag.isdigit():
            continue
        tags.add("#" + tag)
    return tags
