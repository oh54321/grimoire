from __future__ import annotations

import hashlib

import numpy as np
import pytest


class FakeEmbedder:
    """Deterministic hash-derived embedder for fast offline tests.

    Produces L2-normalised float32 vectors of the requested dimension.
    Same phrase -> same vector; small text edits -> nearby vectors only
    by coincidence, so tests that depend on semantic closeness should
    construct vectors directly rather than rely on this.
    """

    model_name = "fake-embedder"

    def __init__(self, dim: int = 16):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, phrase: str) -> np.ndarray:
        seed_bytes = hashlib.sha256(phrase.encode("utf-8")).digest()
        seed = int.from_bytes(seed_bytes[:8], "big", signed=False) % (2**32)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(self._dim).astype(np.float32)
        vec /= np.linalg.norm(vec) + 1e-12
        return vec


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture(scope="session")
def real_embedder():
    """Real sentence-transformers embedder. Skips if model can't load."""
    try:
        from search.embedder import VectorConverter
        emb = VectorConverter()
        emb.encode("warmup")
        return emb
    except Exception as e:
        pytest.skip(f"real embedder unavailable: {e}")
