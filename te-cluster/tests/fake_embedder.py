"""Deterministic stand-in for MiniLM in unit tests: hashed bag-of-words into
384 dims, L2-normalized. Posts that share words point the same way, which is
enough to exercise join-or-seed without the 90 MB model. (The real
model's *semantic* behaviour is covered by test_model.py, which runs inside
the container where the model is baked in.)"""
import hashlib
import re

import numpy as np


class FakeEmbedder:
    dim = 384

    def __init__(self):
        self.calls = []  # batch sizes, to check micro-batching

    def encode(self, texts):
        self.calls.append(len(texts))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out
