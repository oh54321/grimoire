from __future__ import annotations

import pytest

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
