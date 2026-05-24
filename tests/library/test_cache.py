from pathlib import Path

import pytest

from library.cache import NodeCache, CacheStats
from library.config import LibraryConfig
from library.nodes import FolderNode, CodeNode
from library.store import NodeStore


def _store(tmp_path: Path) -> NodeStore:
    return NodeStore(LibraryConfig(root_path=tmp_path, max_description_tokens=10_000))


def test_get_falls_through_to_store_on_miss(tmp_path: Path):
    store = _store(tmp_path)
    store.save(FolderNode(node_id="a", name="x", description="x"))
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)

    node = cache.get("a")
    assert node.node_id == "a"
    s = cache.stats()
    assert s.misses == 1 and s.hits == 0


def test_get_returns_cached_on_second_call(tmp_path: Path):
    store = _store(tmp_path)
    store.save(FolderNode(node_id="a", name="x", description="x"))
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)

    cache.get("a")
    cache.get("a")
    s = cache.stats()
    assert s.hits == 1 and s.misses == 1


def test_put_writes_through_to_store(tmp_path: Path):
    store = _store(tmp_path)
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)
    node = CodeNode(node_id="a", name="f", description="x")
    cache.put(node, code="def f(): pass\n")

    assert store.exists("a")
    assert store.load_code("a") == "def f(): pass\n"


def test_invalidate_drops_entry(tmp_path: Path):
    store = _store(tmp_path)
    store.save(FolderNode(node_id="a", name="x", description="x"))
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)
    cache.get("a")
    cache.invalidate("a")
    assert cache.stats().entry_count == 0


def test_clear_empties_cache(tmp_path: Path):
    store = _store(tmp_path)
    store.save(FolderNode(node_id="a", name="x", description="x"))
    store.save(FolderNode(node_id="b", name="x", description="x"))
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)
    cache.get("a")
    cache.get("b")
    cache.clear()
    assert cache.stats().entry_count == 0
    assert cache.stats().current_bytes == 0


def test_lru_eviction_when_over_budget(tmp_path: Path):
    store = _store(tmp_path)
    for i in range(5):
        store.save(FolderNode(node_id=f"n{i}", name="x", description="x" * 200))
    cache = NodeCache(store, max_bytes=400, ttl_seconds=3600.0)
    for i in range(5):
        cache.get(f"n{i}")
    s = cache.stats()
    assert s.evictions > 0
    assert s.current_bytes <= 400


def test_ttl_expiry_on_access(tmp_path: Path, monkeypatch):
    store = _store(tmp_path)
    store.save(FolderNode(node_id="a", name="x", description="x"))
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=0.1)

    fake_time = [0.0]
    monkeypatch.setattr("library.cache.time.monotonic", lambda: fake_time[0])

    cache.get("a")
    fake_time[0] = 5.0  # well past TTL
    # next access should treat the existing entry as expired and re-fetch from store
    cache.get("a")
    s = cache.stats()
    # 2 misses (initial + after expiry), 0 hits
    assert s.misses == 2
    assert s.hits == 0


def test_get_does_not_load_code(tmp_path: Path):
    store = _store(tmp_path)
    node = CodeNode(node_id="a", name="f", description="x")
    store.save(node, code="def f(): pass\n")
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)

    cache.get("a")
    # The cache must not have pulled code text on a metadata-only get.
    entry = cache._entries["a"]
    assert entry.code is None


def test_get_code_loads_and_caches_code(tmp_path: Path):
    store = _store(tmp_path)
    node = CodeNode(node_id="a", name="f", description="x")
    store.save(node, code="def f(): pass\n")
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)

    code = cache.get_code("a")
    assert code == "def f(): pass\n"
    assert cache._entries["a"].code == "def f(): pass\n"
    # second call is a hit
    cache.get_code("a")
    assert cache.stats().hits >= 1
