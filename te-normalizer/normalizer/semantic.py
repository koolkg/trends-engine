"""NORM-4b: relevance + category by meaning, with the same all-MiniLM-L6-v2
model te-cluster uses (decision NORM-9).

How it decides, in plain terms:
  * Each category is described by a few short example sentences (PROTOTYPES
    below), plus a set of OFF_TOPIC sentences for what marketing is *not*.
  * At startup those sentences are turned into vectors once.
  * Each post is turned into a vector, and compared with every example
    sentence (cosine similarity: 1 = same meaning, 0 = unrelated).
  * The post's category is the one whose closest example is most similar.

The descriptions live here, next to the keyword table in classifier.py,
not in a config file: they change with the code, and the eval script
(scripts/eval_classifier.py) measures every change against the labelled
fixture before it ships.

Anything with `.encode(list[str]) -> np.ndarray (n, dim)` of unit vectors
works as an embedder; unit tests use a deterministic stand-in.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Category keys match classifier.CATEGORIES and the dashboard's filters.
PROTOTYPES: Dict[str, List[str]] = {
    "email": [
        "email marketing and newsletters",
        "writing better email subject lines to raise open rates",
        "our newsletter open rate and click rate this week",
        "a promo email landed in my inbox",
        "welcome emails, abandoned cart emails and unsubscribes",
        "the founder writes a personal note at the top of our newsletter",
    ],
    "social": [
        "social media marketing on Instagram, TikTok and X",
        "a brand's voice and tone in its social media posts",
        "captions, reels, engagement and followers",
        "brands posting user-generated content and product demo videos",
        "our social posts got more saves, shares and comments",
        "influencers and creators promoting a product",
    ],
    "ecommerce": [
        "online shopping, prices, discounts and sales",
        "a cheaper dupe of an expensive name-brand product",
        "a beauty or fashion product sold out at the store and has a restock waitlist",
        "our online store's checkout and conversion rate",
        "customers buying products after seeing them online",
        "free shipping and a limited-time deal",
    ],
    "content": [
        "content marketing and a brand's content strategy",
        "a content trend creators and brands are copying",
        "copywriting and messaging for a campaign",
        "a brand's visual aesthetic and lookbook",
        "short snackable content instead of long polished posts",
        "planning the editorial calendar for our blog",
    ],
}
# Everyday chatter, including look-alikes that share shopping or social words
# with marketing ("tickets sold out", "my dog's video went viral").
OFF_TOPIC: List[str] = [
    "what I cooked for dinner tonight",
    "concert or game tickets sold out in minutes",
    "waiting in line for a show or an event",
    "my dog's video got a lot of views",
    "a funny meme my friend sent me",
    "traffic on my commute this morning",
    "my weekend plans with friends and family",
    "my pet did something funny today",
    "the weather today",
    "learning a new hobby and it is going badly",
    "a sports game last night",
    "feeling tired, going to bed early",
]


@dataclass(frozen=True)
class SemanticResult:
    category: str          # closest marketing category
    similarity: float      # similarity to that category's closest example, -1..1
    off_topic: float       # similarity to the closest off-topic example
    scores: Dict[str, float]

    @property
    def margin(self) -> float:
        """How much more the post looks like marketing than like off-topic chatter."""
        return self.similarity - self.off_topic


class SemanticClassifier:
    def __init__(self, embedder, prototypes: Dict[str, Sequence[str]] = PROTOTYPES,
                 off_topic: Sequence[str] = OFF_TOPIC):
        self._embedder = embedder
        self._labels: List[str] = []
        sentences: List[str] = []
        for cat, examples in prototypes.items():
            for s in examples:
                self._labels.append(cat)
                sentences.append(s)
        for s in off_topic:
            self._labels.append("")   # "" marks an off-topic example
            sentences.append(s)
        self._matrix = np.asarray(embedder.encode(sentences), dtype=np.float32)   # (n_examples, dim)
        self._categories = list(prototypes)
        logger.info("semantic classifier ready: %d categories, %d example sentences",
                    len(self._categories), len(sentences))

    def classify_many(self, texts: Sequence[str]) -> List[SemanticResult]:
        if not texts:
            return []
        vectors = np.asarray(self._embedder.encode(list(texts)), dtype=np.float32)   # (n_posts, dim)
        sims = vectors @ self._matrix.T            # unit vectors, so this is cosine similarity
        out = []
        for row in sims:
            best: Dict[str, float] = {}
            off = -1.0
            for label, s in zip(self._labels, row):
                s = float(s)
                if label:
                    best[label] = max(best.get(label, -1.0), s)
                else:
                    off = max(off, s)
            cat = max(self._categories, key=lambda c: best[c])   # ties: first in PROTOTYPES order
            out.append(SemanticResult(cat, best[cat], off, {c: round(v, 3) for c, v in best.items()}))
        return out

    def classify(self, text: str) -> SemanticResult:
        return self.classify_many([text])[0]


def load_minilm(model_name: str = "sentence-transformers/all-MiniLM-L6-v2", max_tokens: int = 128):
    """The real model, baked into the image at build time (see Dockerfile).
    Same settings as te-cluster's embedder."""
    from sentence_transformers import SentenceTransformer  # heavy import, deferred until needed

    model = SentenceTransformer(model_name, device="cpu")
    model.max_seq_length = max_tokens

    class _MiniLM:
        dim = 384

        def encode(self, texts):
            return model.encode(list(texts), batch_size=max(1, len(texts)), normalize_embeddings=True,
                                convert_to_numpy=True, show_progress_bar=False)

    logger.info("loaded %s for relevance/category", model_name)
    return _MiniLM()
