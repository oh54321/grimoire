import numpy as np
from grimoire.search.tagged_kvdb import TaggedKVDatabase
from tests.search.test_tagged_kvdb_keyed import FakeEmbedder


def test_update_tags_changes_filter_without_reembedding():
    emb = FakeEmbedder()
    db = TaggedKVDatabase(embedder=emb)
    db.add("vector math helper", {"id": "n1"}, tags=["@in:f1"], key="n1")
    before = db.search("vector math helper", 1)[0][1]   # similarity score
    db.update_tags("n1", {"@in:f2"})
    assert db.list_by_tags(["@in:f1"]) == []
    assert [v["id"] for v in db.list_by_tags(["@in:f2"])] == ["n1"]
    after = db.search("vector math helper", 1)[0][1]
    assert before == after            # vector untouched -> identical score
