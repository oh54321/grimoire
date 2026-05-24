from __future__ import annotations

import threading

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


def test_search_paged_returns_pages(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    for i in range(5):
        db.add(f"p{i}", f"v{i}", tags=["t"])

    paged = db.search_paged("p0", page_size=2, tags=["t"])
    assert isinstance(paged, PagedList)
    assert paged.num_pages == 3
    assert len(paged.get_page(0)) == 2


def test_search_paged_respects_max_pages(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    for i in range(10):
        db.add(f"p{i}", f"v{i}", tags=["t"])

    paged = db.search_paged("p0", page_size=2, max_pages=2, tags=["t"])
    assert paged.num_pages == 2
    assert len(paged) == 4


def test_search_paged_validates_args(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    with pytest.raises(ValueError):
        db.search_paged("q", page_size=0)
    with pytest.raises(ValueError):
        db.search_paged("q", page_size=2, max_pages=0)


def test_save_and_load_roundtrip(tmp_path, fake_embedder):
    path = tmp_path / "store"
    db = TaggedKVDatabase(path=path, embedder=fake_embedder)
    db.add("a", "va", tags=["x", "y"])
    db.add("b", "vb", tags=["y"])
    db.save()

    db2 = TaggedKVDatabase(path=path, embedder=fake_embedder)
    assert db2.tags_of("a") == frozenset({"x", "y"})
    assert db2.tags_of("b") == frozenset({"y"})
    assert set(db2.list_by_tags(["y"])) == {"va", "vb"}
    assert set(db2.list_by_tags(["x", "y"])) == {"va"}


def test_load_v1_into_tagged_kvdatabase_rejects(tmp_path, fake_embedder):
    from search import KVDatabase

    path = tmp_path / "store"
    plain = KVDatabase(path=path, embedder=fake_embedder)
    plain.add("a", "va")
    plain.save()

    with pytest.raises(ValueError, match="unsupported store version"):
        TaggedKVDatabase(path=path, embedder=fake_embedder)


def test_load_v2_into_plain_kvdatabase_rejects(tmp_path, fake_embedder):
    from search import KVDatabase

    path = tmp_path / "store"
    tagged = TaggedKVDatabase(path=path, embedder=fake_embedder)
    tagged.add("a", "va", tags=["x"])
    tagged.save()

    with pytest.raises(ValueError, match="unsupported store version"):
        KVDatabase(path=path, embedder=fake_embedder)


def test_tag_to_ids_rebuilt_after_load_supports_search(tmp_path, fake_embedder):
    path = tmp_path / "store"
    db = TaggedKVDatabase(path=path, embedder=fake_embedder)
    db.add("a", "va", tags=["x"])
    db.add("b", "vb", tags=["x", "y"])
    db.save()

    db2 = TaggedKVDatabase(path=path, embedder=fake_embedder)
    results = db2.search("a", n=10, tags=["x"])
    assert {v for v, _ in results} == {"va", "vb"}


def test_concurrent_readers_and_writer(fake_embedder):
    db = TaggedKVDatabase(embedder=fake_embedder)
    for i in range(50):
        db.add(f"p{i}", f"v{i}", tags=["t"])

    stop = threading.Event()
    errors: list[BaseException] = []

    def reader():
        try:
            while not stop.is_set():
                db.search("p0", n=5, tags=["t"])
                db.list_by_tags(["t"])
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    def writer():
        try:
            for i in range(50, 100):
                db.add(f"p{i}", f"v{i}", tags=["t"])
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    w = threading.Thread(target=writer)
    for r in readers:
        r.start()
    w.start()
    w.join()
    stop.set()
    for r in readers:
        r.join()

    assert errors == []
    assert len(db) == 100
    assert set(db.list_by_tags(["t"])) == {f"v{i}" for i in range(100)}
