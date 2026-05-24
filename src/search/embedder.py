from __future__ import annotations

import threading
from typing import Any

import numpy as np


class VectorConverter:
    """Wraps a sentence-transformers model to produce L2-normalised float32 vectors.

    The model is lazy-loaded on first encode. encode() is serialised by an
    internal lock since sentence-transformers is not safe under concurrent calls.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name: str = model_name
        self._model: Any = None
        self._dim: int | None = None
        self._lock: threading.Lock = threading.Lock()

    def _ensure_model(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
            self._dim = int(getter())

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._ensure_model()
        return self._dim  # type: ignore[return-value]

    def encode(self, phrase: str) -> np.ndarray:
        with self._lock:
            self._ensure_model()
            vec = self._model.encode(phrase, normalize_embeddings=True)  # type: ignore[union-attr]
        vec = np.asarray(vec, dtype=np.float32)
        return vec
