"""CLU-2 (decision CLU-9): the real all-MiniLM-L6-v2 sentence-embedding model,
as the design doc specifies. It's the only option that groups posts by meaning
rather than shared words, which is what catches a trend before it has a shared
hashtag or phrase.

Served exactly as the Low Level Details tab describes: plain
sentence-transformers on CPU PyTorch, inputs truncated to 128 tokens, outputs
L2-normalized (so cosine similarity is a plain dot product), and called once
per micro-batch rather than once per post. The model is downloaded at image
build time (see Dockerfile), so the container runs offline.

Anything with `.dim` and `.encode(list[str]) -> np.ndarray (n, dim)` of unit
vectors works as an embedder; the tests use a deterministic stand-in.
"""
import logging
from typing import List

import numpy as np

logger = logging.getLogger(__name__)


class MiniLMEmbedder:
    dim = 384

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", max_tokens: int = 128):
        from sentence_transformers import SentenceTransformer  # heavy import, deferred until needed

        self._model = SentenceTransformer(model_name, device="cpu")
        self._model.max_seq_length = max_tokens
        logger.info("loaded %s (dim %d, max %d tokens)", model_name, self.dim, max_tokens)

    def encode(self, texts: List[str]) -> np.ndarray:
        vectors = self._model.encode(texts, batch_size=len(texts) or 1, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)
