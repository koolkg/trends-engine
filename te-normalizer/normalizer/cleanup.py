"""NORM-2: text cleanup. Produces the `cleaned_text` stored in
normalized_content_item and sent downstream.

Deliberately conservative: it strips things that are never content (markup,
URLs, control and zero-width characters, runs of whitespace) and leaves
everything a downstream stage might want. Hashtags and @mentions stay, because
te-detector's term extraction (DET-2) keys on hashtags. Emoji and casing stay
too, since the embedding model in te-cluster reads them fine.
"""
import html
import re
import unicodedata

_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WS = re.compile(r"\s+")


def clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = _TAG.sub(" ", text)
    text = unicodedata.normalize("NFKC", text)
    text = _URL.sub(" ", text)
    # Control/format chars (newlines, tabs, zero-width spaces, BOM, ...) -> space.
    # ZWJ inside emoji sequences goes too; the emoji themselves survive.
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    return _WS.sub(" ", text).strip()
