import numpy as np
from search.tagged_kvdb import TaggedKVDatabase


class CountingEmbedder:
    model_name = "fake"
    dim = 8

    def __init__(self):
        self.calls = 0

    def encode(self, phrase: str) -> np.ndarray:
        self.calls += 1
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def _db(emb):
    db = TaggedKVDatabase(embedder=emb)
    db.add("a", {"id": "n1"}, tags=["@kind:method", "@in:f1", "topic"], key="n1")
    db.add("b", {"id": "n2"}, tags=["@kind:class", "@in:f1", "topic"], key="n2")
    db.add("c", {"id": "n3"}, tags=["@kind:method", "@in:f2"], key="n3")
    return db


def test_and_tags_with_or_groups():
    emb = CountingEmbedder()
    db = _db(emb)
    res = db.search_filtered("a", 10, all_tags=["topic"],
                             any_groups=[{"@in:f1", "@in:f2"}, {"@kind:method", "@kind:class"}])
    got = {v["id"] for v, _ in res}
    assert got == {"n1", "n2"}            # n3 lacks "topic"


def test_embeds_exactly_once():
    emb = CountingEmbedder()
    db = _db(emb)
    emb.calls = 0
    db.search_filtered("query", 10, all_tags=[], any_groups=[{"@in:f1"}, {"@kind:method"}])
    assert emb.calls == 1


def test_no_groups_equals_plain_search():
    emb = CountingEmbedder()
    db = _db(emb)
    a = {v["id"] for v, _ in db.search_filtered("a", 10, all_tags=["topic"])}
    b = {v["id"] for v, _ in db.search("a", 10, tags=["topic"])}
    assert a == b
