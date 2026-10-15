"""The real all-MiniLM-L6-v2: only meaningful where the model is installed
(the container image bakes it in). Skipped elsewhere."""
import unittest

import numpy as np

try:
    from cluster.embedder import MiniLMEmbedder
    import sentence_transformers  # noqa: F401
    HAVE_MODEL = True
except ImportError:
    HAVE_MODEL = False


@unittest.skipUnless(HAVE_MODEL, "sentence-transformers not installed (runs in the container)")
class TestMiniLM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = MiniLMEmbedder()

    def test_shape_and_unit_length(self):
        v = self.model.encode(["one", "two words"])
        self.assertEqual(v.shape, (2, 384))
        np.testing.assert_allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)

    def test_paraphrases_without_shared_hashtag_are_closer_than_unrelated(self):
        a, b, c = self.model.encode([
            "every brand account is suddenly typing in all lowercase",
            "why are companies writing their captions without capital letters now",
            "my sourdough starter died again",
        ])
        self.assertGreater(float(a @ b), float(a @ c) + 0.2)
