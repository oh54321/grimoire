from __future__ import annotations

import pytest

from search.pages import PagedList
from search.tagged_kvdb import TaggedKVDatabase


def test_add_with_tags_then_tags_of(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("alpha", {"id": 1}, tags=["x", "y"])

    assert db.tags_of("alpha") == frozenset({"x", "y"})


def test_add_without_tags_defaults_to_empty(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("alpha", "v")

    assert db.tags_of("alpha") == frozenset()


def test_all_tags_aggregates(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("a", 1, tags=["x", "y"])
    db.add("b", 2, tags=["y", "z"])

    assert db.all_tags() == {"x", "y", "z"}


def test_tags_of_unknown_phrase_raises(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    with pytest.raises(KeyError):
        db.tags_of("missing")


def test_readd_replaces_tags_and_prunes_empty_buckets(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("a", 1, tags=["only-on-a"])
    db.add("a", 2, tags=["new"])

    assert db.tags_of("a") == frozenset({"new"})
    assert "only-on-a" not in db.all_tags()
    assert len(db) == 1


def test_rejects_non_string_tag(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    with pytest.raises(TypeError):
        db.add("a", 1, tags=["ok", 5])


def test_rejects_empty_string_tag(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    with pytest.raises(TypeError):
        db.add("a", 1, tags=["ok", ""])


def test_contains_and_len(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    assert "a" not in db
    assert len(db) == 0
    db.add("a", 1, tags=["t"])
    assert "a" in db
    assert len(db) == 1


def _seed(db):
    db.add("a", "va", tags=["x"])
    db.add("b", "vb", tags=["x", "y"])
    db.add("c", "vc", tags=["y"])
    db.add("d", "vd", tags=["x", "y", "z"])


def test_list_by_tags_and_semantics(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    _seed(db)

    assert set(db.list_by_tags(["x", "y"])) == {"vb", "vd"}
    assert set(db.list_by_tags(["x"])) == {"va", "vb", "vd"}
    assert set(db.list_by_tags(["x", "y", "z"])) == {"vd"}


def test_list_by_tags_empty_filter_returns_all(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    _seed(db)
    assert set(db.list_by_tags([])) == {"va", "vb", "vc", "vd"}


def test_list_by_tags_unknown_tag_returns_empty(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    _seed(db)
    assert db.list_by_tags(["x", "nope"]) == []


def test_list_by_tags_is_stable_id_order(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    _seed(db)
    # b was added before d; both carry x and y
    assert db.list_by_tags(["x", "y"]) == ["vb", "vd"]


def test_list_by_tags_paged(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    _seed(db)

    paged = db.list_by_tags_paged(["x"], page_size=2)
    assert isinstance(paged, PagedList)
    assert paged.num_pages == 2
    assert list(paged.get_page(0)) == ["va", "vb"]
    assert list(paged.get_page(1)) == ["vd"]


def test_list_by_tags_paged_validates_args(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    with pytest.raises(ValueError):
        db.list_by_tags_paged([], page_size=0)
    with pytest.raises(ValueError):
        db.list_by_tags_paged([], page_size=2, max_pages=0)


import numpy as np


def test_search_no_filter_matches_kvdatabase_shape(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("alpha", "va", tags=["x"])
    db.add("beta", "vb", tags=["y"])

    results = db.search("alpha", n=2)
    assert results[0][0] == "va"
    assert results[0][1] == pytest.approx(1.0, abs=1e-4)


def test_search_and_filter_restricts_results(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("a", "va", tags=["x"])
    db.add("b", "vb", tags=["x", "y"])
    db.add("c", "vc", tags=["y"])

    results = db.search("a", n=10, tags=["x", "y"])
    values = {v for v, _ in results}
    assert values == {"vb"}


def test_search_unknown_tag_returns_empty(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("a", "va", tags=["x"])
    assert db.search("a", n=5, tags=["nope"]) == []


def test_search_empty_db_returns_empty(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    assert db.search("a", n=5) == []
    assert db.search("a", n=5, tags=["x"]) == []


def test_search_n_zero_returns_empty(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    db.add("a", "va", tags=["x"])
    assert db.search("a", n=0, tags=["x"]) == []


def test_search_brute_force_and_hnsw_agree(fake_embedder):
    def seed(db):
        for i in range(20):
            db.add(f"phrase-{i}", f"v{i}", tags=["t"])

    db_bf = TaggedKVDatabase(embedder=fake_embedder, brute_force_threshold=10_000)
    db_hn = TaggedKVDatabase(embedder=fake_embedder, brute_force_threshold=0)
    seed(db_bf)
    seed(db_hn)

    r_bf = db_bf.search("phrase-3", n=5, tags=["t"])
    r_hn = db_hn.search("phrase-3", n=5, tags=["t"])

    assert [v for v, _ in r_bf] == [v for v, _ in r_hn]
    for (_, s_bf), (_, s_hn) in zip(r_bf, r_hn):
        assert s_bf == pytest.approx(s_hn, abs=1e-4)


def test_search_results_ordered_by_similarity_descending(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    for i in range(10):
        db.add(f"p{i}", f"v{i}", tags=["t"])

    results = db.search("p0", n=5, tags=["t"])
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_search_threshold_zero_uses_hnsw_path(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder, brute_force_threshold=0)
    db.add("only", "v", tags=["t"])
    results = db.search("only", n=1, tags=["t"])
    assert results == [("v", pytest.approx(1.0, abs=1e-4))]
