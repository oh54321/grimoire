from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from search import KVDatabase


def test_add_then_search_returns_value_with_high_similarity(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    db.add("hello world", {"id": 1})

    results = db.search("hello world", n=1)

    assert len(results) == 1
    value, score = results[0]
    assert value == {"id": 1}
    assert score == pytest.approx(1.0, abs=1e-4)


def test_search_orders_by_similarity(fake_embedder):
    # Inject vectors via monkeypatching so we control ordering deterministically.
    db = KVDatabase(embedder=fake_embedder)
    db.add("a", "value_a")
    db.add("b", "value_b")
    db.add("c", "value_c")

    results = db.search("a", n=3)
    assert results[0][0] == "value_a"
    assert results[0][1] >= results[1][1] >= results[2][1]


def test_overwrite_replaces_value(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    db.add("p", "v1")
    db.add("p", "v2")

    results = db.search("p", n=5)
    values = [v for v, _ in results]
    assert "v2" in values
    assert "v1" not in values
    assert len(db) == 1


def test_contains_and_len(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    assert len(db) == 0
    assert "missing" not in db

    db.add("hello", 1)
    assert len(db) == 1
    assert "hello" in db

    db.add("hello", 2)
    assert len(db) == 1


def test_empty_db_search_returns_empty_list(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    assert db.search("anything", n=5) == []


def test_n_larger_than_size_returns_all(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    db.add("a", 1)
    db.add("b", 2)

    results = db.search("a", n=100)
    assert len(results) == 2


def test_add_rejects_non_json_serialisable(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    with pytest.raises(TypeError):
        db.add("phrase", {1, 2, 3})  # set is not JSON-serialisable


def test_save_load_round_trip(tmp_path, fake_embedder):
    path = tmp_path / "db"
    db = KVDatabase(path=path, embedder=fake_embedder)
    db.add("alpha", {"x": 1})
    db.add("beta", [1, 2, 3])
    db.add("gamma", "string-value")
    db.save()

    assert (path / "index.bin").exists()
    assert (path / "store.json").exists()

    db2 = KVDatabase(path=path, embedder=fake_embedder)
    assert len(db2) == 3
    assert "alpha" in db2

    results = db2.search("alpha", n=1)
    assert results[0][0] == {"x": 1}


def test_save_without_path_raises(fake_embedder):
    db = KVDatabase(embedder=fake_embedder)
    db.add("a", 1)
    with pytest.raises(RuntimeError):
        db.save()


def test_load_rejects_mismatched_dim(tmp_path, fake_embedder):
    from tests.search.conftest import FakeEmbedder

    path = tmp_path / "db"
    db = KVDatabase(path=path, embedder=fake_embedder)
    db.add("a", 1)
    db.save()

    other = FakeEmbedder(dim=32)
    with pytest.raises(ValueError, match="dim"):
        KVDatabase(path=path, embedder=other)


def test_load_rejects_mismatched_model_name(tmp_path, fake_embedder, monkeypatch):
    from tests.search.conftest import FakeEmbedder

    path = tmp_path / "db"
    db = KVDatabase(path=path, embedder=fake_embedder)
    db.add("a", 1)
    db.save()

    other = FakeEmbedder()
    other.model_name = "different-model"
    with pytest.raises(ValueError, match="model"):
        KVDatabase(path=path, embedder=other)


def test_resize_past_initial_capacity(fake_embedder):
    db = KVDatabase(embedder=fake_embedder, initial_capacity=8)
    for i in range(20):
        db.add(f"phrase-{i}", i)

    assert len(db) == 20
    results = db.search("phrase-7", n=1)
    assert results[0][0] == 7
