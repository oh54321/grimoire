import numpy as np
import pytest
from grimoire.search.tagged_kvdb import TaggedKVDatabase


class FakeEmbedder:
    model_name = "fake"
    dim = 8

    def encode(self, phrase: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def test_identical_phrases_distinct_keys_both_persist():
    db = TaggedKVDatabase(embedder=FakeEmbedder())
    db.add("parse the input", {"id": "n1"}, tags=["a"], key="n1")
    db.add("parse the input", {"id": "n2"}, tags=["b"], key="n2")  # same phrase, different key
    assert len(db) == 2
    assert {tuple(v.items()) for v in db.list_by_tags(["a"])} == {(("id", "n1"),)}
    assert {tuple(v.items()) for v in db.list_by_tags(["b"])} == {(("id", "n2"),)}


def test_delete_by_key_removes_entry():
    db = TaggedKVDatabase(embedder=FakeEmbedder())
    db.add("x", {"id": "n1"}, tags=["a"], key="n1")
    db.delete("n1")
    assert len(db) == 0
    assert db.list_by_tags(["a"]) == []
    db.delete("n1")  # idempotent, no raise
