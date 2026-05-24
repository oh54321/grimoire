from __future__ import annotations

import numpy as np
import pytest


def test_encode_returns_normalised_float32_vector(real_embedder):
    vec = real_embedder.encode("hello world")
    assert vec.dtype == np.float32
    assert vec.shape == (real_embedder.dim,)
    assert np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-4)


def test_encode_is_deterministic(real_embedder):
    a = real_embedder.encode("the quick brown fox")
    b = real_embedder.encode("the quick brown fox")
    np.testing.assert_array_equal(a, b)


def test_encode_different_inputs_differ(real_embedder):
    a = real_embedder.encode("apples")
    b = real_embedder.encode("orbital mechanics")
    assert not np.array_equal(a, b)
