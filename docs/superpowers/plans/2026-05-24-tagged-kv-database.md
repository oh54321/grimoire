# Tagged KV Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `TaggedKVDatabase` to `src/search/` — same phrase→value vector store as `KVDatabase`, but each entry carries an immutable set of tags, and searches can filter (AND-only) by tags or list everything matching a tag filter.

**Architecture:** Extract a `_VectorStoreBase` that owns the lock, HNSW index, and shared `_Store`. `KVDatabase` becomes a thin subclass with no extra state. `TaggedKVDatabase` is a sibling subclass that adds `id_to_tags` (canonical, persisted) and `tag_to_ids` (derived inverted index, rebuilt on load). Filtered search is adaptive: brute-force exact cosine when `|allowed| <= brute_force_threshold`, otherwise HNSW with a `filter=` callback.

**Tech Stack:** Python 3.x, `hnswlib`, `numpy`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-24-tagged-kv-database-design.md`

---

## File Structure

- **Create:** `src/search/_base.py` — `_VectorStoreBase` and `_Store` (the shared lock/index/store machinery extracted from today's `KVDatabase`).
- **Create:** `src/search/tagged_kvdb.py` — `TaggedKVDatabase`.
- **Create:** `tests/search/test_tagged_kvdb.py` — all new tests.
- **Modify:** `src/search/kvdb.py` — slim down to a subclass of `_VectorStoreBase`.
- **Modify:** `src/search/__init__.py` — re-export `TaggedKVDatabase`.

`tests/search/test_kvdb.py` and `tests/search/test_kvdb_concurrency.py` are NOT modified — they serve as the "refactor is behaviour-preserving" gate after Task 1.

---

## Task 1: Extract `_VectorStoreBase` (behaviour-preserving refactor)

**Goal:** Move the lock/index/store skeleton out of `kvdb.py` into a new `_base.py`, with two hooks (`_extra_save_data`, `_extra_load_data`) for subclass payloads. `KVDatabase` becomes a thin subclass. No public behaviour changes; existing tests must continue to pass without modification.

**Files:**
- Create: `src/search/_base.py`
- Modify: `src/search/kvdb.py`
- Test: `tests/search/test_kvdb.py` and `tests/search/test_kvdb_concurrency.py` (run unchanged)

### Steps

- [ ] **Step 1: Run the existing search test suite to establish a baseline.**

Run: `pytest tests/search -v`
Expected: all tests pass. Record the count.

- [ ] **Step 2: Create `src/search/_base.py` with the extracted base class.**

```python
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import hnswlib
import numpy as np

from search.pages import PagedList

JSONValue = None | bool | int | float | str | list | dict

STORE_VERSION = 1


class ReadWriteLock:
    """Writer-preference reader/writer lock with reentrant writer."""

    def __init__(self) -> None:
        self._cond: threading.Condition = threading.Condition(threading.Lock())
        self._readers: int = 0
        self._writers_waiting: int = 0
        self._writer_thread: int | None = None
        self._writer_depth: int = 0

    def acquire_read(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer_thread == me:
                self._readers += 1
                return
            while self._writer_thread is not None or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer_thread == me:
                self._writer_depth += 1
                return
            self._writers_waiting += 1
            try:
                while self._writer_thread is not None or self._readers > 0:
                    self._cond.wait()
            finally:
                self._writers_waiting -= 1
            self._writer_thread = me
            self._writer_depth = 1

    def release_write(self) -> None:
        with self._cond:
            self._writer_depth -= 1
            if self._writer_depth == 0:
                self._writer_thread = None
                self._cond.notify_all()

    def read(self) -> "_LockCtx":
        return _LockCtx(self.acquire_read, self.release_read)

    def write(self) -> "_LockCtx":
        return _LockCtx(self.acquire_write, self.release_write)


class _LockCtx:
    def __init__(self, acq: Callable[[], None], rel: Callable[[], None]) -> None:
        self._acq: Callable[[], None] = acq
        self._rel: Callable[[], None] = rel

    def __enter__(self) -> "_LockCtx":
        self._acq()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._rel()
        return False


@dataclass
class _Store:
    phrase_to_id: dict[str, int] = field(default_factory=dict)
    id_to_value: dict[int, JSONValue] = field(default_factory=dict)
    id_to_phrase: dict[int, str] = field(default_factory=dict)
    next_id: int = 0
    dim: int = 0
    model_name: str = ""


class _VectorStoreBase:
    """Shared lock + HNSW + persistence skeleton for vector KV databases.

    Subclasses extend persistence via `_extra_save_data` / `_extra_load_data`
    and choose their own `_store_version`. The base persists `_Store` and
    `index.bin` atomically.
    """

    _store_version: int = STORE_VERSION

    def __init__(
        self,
        path: Path | None = None,
        embedder=None,
        initial_capacity: int = 1024,
    ) -> None:
        if embedder is None:
            from search.embedder import VectorConverter
            embedder = VectorConverter()
        self._embedder = embedder
        self._path = Path(path) if path is not None else None
        self._lock = ReadWriteLock()
        self._capacity = max(1, initial_capacity)

        self._store = _Store(dim=embedder.dim, model_name=embedder.model_name)
        self._index = hnswlib.Index(space="cosine", dim=embedder.dim)
        self._index.init_index(max_elements=self._capacity, ef_construction=200, M=16)
        self._index.set_ef(50)

        if self._path is not None and (self._path / "store.json").exists():
            self.load()

    # ---- subclass hooks --------------------------------------------------
    def _extra_save_data(self) -> dict:
        return {}

    def _extra_load_data(self, data: dict) -> None:
        return None

    # ---- shared helpers --------------------------------------------------
    def _encode(self, phrase: str):
        return self._embedder.encode(phrase)

    def _grow_for(self, new_id: int) -> None:
        if new_id >= self._capacity:
            self._capacity *= 2
            self._index.resize_index(self._capacity)

    # ---- persistence -----------------------------------------------------
    def save(self) -> None:
        if self._path is None:
            raise RuntimeError(f"{type(self).__name__} has no path; cannot save")
        with self._lock.read():
            self._path.mkdir(parents=True, exist_ok=True)
            index_path = self._path / "index.bin"
            store_path = self._path / "store.json"
            index_tmp = index_path.with_suffix(".bin.tmp")
            store_tmp = store_path.with_suffix(".json.tmp")

            self._index.save_index(str(index_tmp))

            data = {
                "version": self._store_version,
                "model_name": self._store.model_name,
                "dim": self._store.dim,
                "next_id": self._store.next_id,
                "capacity": self._capacity,
                "phrase_to_id": self._store.phrase_to_id,
                "id_to_phrase": {str(k): v for k, v in self._store.id_to_phrase.items()},
                "id_to_value": {str(k): v for k, v in self._store.id_to_value.items()},
            }
            data.update(self._extra_save_data())
            store_tmp.write_text(json.dumps(data))

            os.replace(index_tmp, index_path)
            os.replace(store_tmp, store_path)

    def load(self) -> None:
        if self._path is None:
            raise RuntimeError(f"{type(self).__name__} has no path; cannot load")
        with self._lock.write():
            store_path = self._path / "store.json"
            index_path = self._path / "index.bin"
            data = json.loads(store_path.read_text())

            if data.get("version") != self._store_version:
                raise ValueError(
                    f"unsupported store version for {type(self).__name__}: "
                    f"file={data.get('version')} expected={self._store_version}"
                )
            if data["dim"] != self._embedder.dim:
                raise ValueError(
                    f"dim mismatch: store={data['dim']} embedder={self._embedder.dim}"
                )
            if data["model_name"] != self._embedder.model_name:
                raise ValueError(
                    f"model mismatch: store={data['model_name']!r} "
                    f"embedder={self._embedder.model_name!r}"
                )

            self._capacity = max(self._capacity, int(data.get("capacity", self._capacity)))
            self._index = hnswlib.Index(space="cosine", dim=self._embedder.dim)
            self._index.load_index(str(index_path), max_elements=self._capacity)
            self._index.set_ef(50)

            self._store = _Store(
                phrase_to_id=dict(data["phrase_to_id"]),
                id_to_value={int(k): v for k, v in data["id_to_value"].items()},
                id_to_phrase={int(k): v for k, v in data["id_to_phrase"].items()},
                next_id=int(data["next_id"]),
                dim=int(data["dim"]),
                model_name=str(data["model_name"]),
            )
            self._extra_load_data(data)

    # ---- dunder ---------------------------------------------------------
    def __len__(self) -> int:
        with self._lock.read():
            return len(self._store.id_to_value)

    def __contains__(self, phrase: str) -> bool:
        with self._lock.read():
            return phrase in self._store.phrase_to_id
```

- [ ] **Step 3: Slim down `src/search/kvdb.py` to subclass the base.**

Replace the entire file with:

```python
from __future__ import annotations

import json
from typing import Optional, Tuple

import numpy as np

from search._base import (
    JSONValue,
    STORE_VERSION,
    _LockCtx,
    _Store,
    _VectorStoreBase,
    ReadWriteLock,
)
from search.pages import PagedList

__all__ = [
    "JSONValue",
    "STORE_VERSION",
    "ReadWriteLock",
    "KVDatabase",
]


class KVDatabase(_VectorStoreBase):
    _store_version = STORE_VERSION

    def add(self, phrase: str, value: JSONValue) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"value not JSON-serialisable: {e}") from e

        vec = self._encode(phrase)

        with self._lock.write():
            old_id = self._store.phrase_to_id.get(phrase)
            if old_id is not None:
                self._index.mark_deleted(old_id)
                del self._store.id_to_value[old_id]
                del self._store.id_to_phrase[old_id]

            new_id = self._store.next_id
            self._store.next_id += 1
            self._grow_for(new_id)

            self._index.add_items(vec.reshape(1, -1), np.array([new_id]))
            self._store.phrase_to_id[phrase] = new_id
            self._store.id_to_value[new_id] = value
            self._store.id_to_phrase[new_id] = phrase

    def search(self, phrase: str, n: int) -> list[tuple[JSONValue, float]]:
        vec = self._encode(phrase)
        with self._lock.read():
            live = len(self._store.id_to_value)
            if live == 0 or n <= 0:
                return []
            k = min(n, live)
            self._index.set_num_threads(1)
            ids, distances = self._index.knn_query(vec.reshape(1, -1), k=k)
            out: list[tuple[JSONValue, float]] = []
            for idx, dist in zip(ids[0], distances[0]):
                idx = int(idx)
                if idx not in self._store.id_to_value:
                    continue
                similarity = 1.0 - float(dist)
                out.append((self._store.id_to_value[idx], similarity))
            return out

    def search_paged(
        self,
        phrase: str,
        page_size: int,
        max_pages: Optional[int] = None,
    ) -> PagedList[Tuple[JSONValue, float]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")

        with self._lock.read():
            live = len(self._store.id_to_value)
            n = live if max_pages is None else min(live, page_size * max_pages)
            results = self.search(phrase, n)
            return PagedList(results, page_size)
```

- [ ] **Step 4: Re-run the full search test suite to confirm the refactor is behaviour-preserving.**

Run: `pytest tests/search -v`
Expected: same pass count as Step 1, zero failures, zero new tests. Any failure here means the refactor changed observable behaviour — fix before proceeding.

- [ ] **Step 5: Commit.**

```bash
git add src/search/_base.py src/search/kvdb.py
git commit -m "refactor(search): extract _VectorStoreBase for KVDatabase"
```

---

## Task 2: `TaggedKVDatabase` skeleton — `add`, `tags_of`, `all_tags`, `__len__`, `__contains__`

**Goal:** Land the class shell with tag state, `add()` enforcing AND-only-friendly invariants, and the simple introspection methods. No search yet.

**Files:**
- Create: `src/search/tagged_kvdb.py`
- Create: `tests/search/test_tagged_kvdb.py`
- Modify: `src/search/__init__.py`

### Steps

- [ ] **Step 1: Write the failing tests.**

Create `tests/search/test_tagged_kvdb.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `pytest tests/search/test_tagged_kvdb.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'search.tagged_kvdb'`.

- [ ] **Step 3: Create `src/search/tagged_kvdb.py` with the skeleton.**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np

from search._base import JSONValue, _VectorStoreBase

TAGGED_STORE_VERSION = 2
DEFAULT_BRUTE_FORCE_THRESHOLD = 1000


def _validate_tags(tags: Iterable[str]) -> frozenset[str]:
    tag_list = list(tags)
    for t in tag_list:
        if not isinstance(t, str) or t == "":
            raise TypeError(f"tag must be a non-empty string, got {t!r}")
    return frozenset(tag_list)


class TaggedKVDatabase(_VectorStoreBase):
    """KVDatabase + per-entry tag set. AND-only tag filters on search."""

    _store_version = TAGGED_STORE_VERSION

    def __init__(
        self,
        path: Path | None = None,
        embedder=None,
        initial_capacity: int = 1024,
        brute_force_threshold: int = DEFAULT_BRUTE_FORCE_THRESHOLD,
    ) -> None:
        if brute_force_threshold < 0:
            raise ValueError("brute_force_threshold must be >= 0")
        self._brute_force_threshold = brute_force_threshold
        self._id_to_tags: dict[int, frozenset[str]] = {}
        self._tag_to_ids: dict[str, set[int]] = {}
        super().__init__(path=path, embedder=embedder, initial_capacity=initial_capacity)

    # ---- mutation -------------------------------------------------------
    def add(
        self,
        phrase: str,
        value: JSONValue,
        tags: Iterable[str] = (),
    ) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"value not JSON-serialisable: {e}") from e

        tag_set = _validate_tags(tags)
        vec = self._encode(phrase)

        with self._lock.write():
            old_id = self._store.phrase_to_id.get(phrase)
            if old_id is not None:
                self._index.mark_deleted(old_id)
                del self._store.id_to_value[old_id]
                del self._store.id_to_phrase[old_id]
                self._remove_id_from_tags(old_id)

            new_id = self._store.next_id
            self._store.next_id += 1
            self._grow_for(new_id)

            self._index.add_items(vec.reshape(1, -1), np.array([new_id]))
            self._store.phrase_to_id[phrase] = new_id
            self._store.id_to_value[new_id] = value
            self._store.id_to_phrase[new_id] = phrase
            self._add_id_to_tags(new_id, tag_set)

    def _add_id_to_tags(self, id_: int, tags: frozenset[str]) -> None:
        self._id_to_tags[id_] = tags
        for t in tags:
            self._tag_to_ids.setdefault(t, set()).add(id_)

    def _remove_id_from_tags(self, id_: int) -> None:
        old_tags = self._id_to_tags.pop(id_, frozenset())
        for t in old_tags:
            bucket = self._tag_to_ids.get(t)
            if bucket is None:
                continue
            bucket.discard(id_)
            if not bucket:
                del self._tag_to_ids[t]

    # ---- introspection --------------------------------------------------
    def tags_of(self, phrase: str) -> frozenset[str]:
        with self._lock.read():
            id_ = self._store.phrase_to_id.get(phrase)
            if id_ is None:
                raise KeyError(phrase)
            return self._id_to_tags.get(id_, frozenset())

    def all_tags(self) -> set[str]:
        with self._lock.read():
            return set(self._tag_to_ids.keys())
```

- [ ] **Step 4: Add the re-export.**

Modify `src/search/__init__.py`:

```python
from search.kvdb import KVDatabase
from search.tagged_kvdb import TaggedKVDatabase
from search.embedder import VectorConverter

__all__ = ["KVDatabase", "TaggedKVDatabase", "VectorConverter"]
```

- [ ] **Step 5: Run the new tests to verify they pass.**

Run: `pytest tests/search/test_tagged_kvdb.py -v`
Expected: all 8 tests pass.

- [ ] **Step 6: Re-run the whole search suite to confirm nothing else regressed.**

Run: `pytest tests/search -v`
Expected: all green (existing tests + the 8 new ones).

- [ ] **Step 7: Commit.**

```bash
git add src/search/tagged_kvdb.py src/search/__init__.py tests/search/test_tagged_kvdb.py
git commit -m "feat(search): TaggedKVDatabase skeleton (add, tags_of, all_tags)"
```

---

## Task 3: `list_by_tags` and `list_by_tags_paged`

**Goal:** Implement the no-vector-search "give me everything matching this tag filter" path, plus the paged variant. This isolates the `_intersect_tag_ids` helper before search depends on it.

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Modify: `tests/search/test_tagged_kvdb.py`

### Steps

- [ ] **Step 1: Append the failing tests to `tests/search/test_tagged_kvdb.py`.**

```python
from search.pages import PagedList


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
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "list_by_tags"`
Expected: AttributeError — `list_by_tags`/`list_by_tags_paged` do not exist.

- [ ] **Step 3: Add the intersection helper and the two list methods to `tagged_kvdb.py`.**

Add these imports at the top:

```python
from typing import Iterable, Optional

from search.pages import PagedList
```

Add these methods to `TaggedKVDatabase` (alongside `tags_of`):

```python
    # ---- filter helper --------------------------------------------------
    def _intersect_tag_ids(self, tags: Iterable[str]) -> set[int] | None:
        """Return the set of ids matching all `tags` (AND).

        Returns `None` when `tags` is empty (sentinel: no filter). Returns
        an empty set if any tag is unknown or the intersection is empty.
        Must be called under the read or write lock.
        """
        tag_list = list(tags)
        for t in tag_list:
            if not isinstance(t, str):
                raise TypeError(f"tag must be a string, got {t!r}")
        if not tag_list:
            return None

        buckets: list[set[int]] = []
        for t in tag_list:
            bucket = self._tag_to_ids.get(t)
            if bucket is None:
                return set()
            buckets.append(bucket)

        buckets.sort(key=len)
        result = set(buckets[0])
        for b in buckets[1:]:
            result &= b
            if not result:
                break
        return result

    # ---- list-by-tags ---------------------------------------------------
    def list_by_tags(self, tags: Iterable[str]) -> list[JSONValue]:
        with self._lock.read():
            allowed = self._intersect_tag_ids(tags)
            if allowed is None:
                ids = sorted(self._store.id_to_value.keys())
            else:
                ids = sorted(allowed)
            return [self._store.id_to_value[i] for i in ids]

    def list_by_tags_paged(
        self,
        tags: Iterable[str],
        page_size: int,
        max_pages: Optional[int] = None,
    ) -> PagedList[JSONValue]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")

        with self._lock.read():
            items = self.list_by_tags(tags)
            if max_pages is not None:
                items = items[: page_size * max_pages]
            return PagedList(items, page_size)
```

- [ ] **Step 4: Run the list tests to verify they pass.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "list_by_tags"`
Expected: all 6 list-related tests pass.

- [ ] **Step 5: Re-run the full suite.**

Run: `pytest tests/search -v`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add src/search/tagged_kvdb.py tests/search/test_tagged_kvdb.py
git commit -m "feat(search): TaggedKVDatabase.list_by_tags + paged variant"
```

---

## Task 4: Filtered `search()` — brute-force and HNSW-filter paths

**Goal:** Implement the adaptive `search()` method. Both code paths (brute force and HNSW with `filter=`) must be exercised by tests, and tests must demonstrate they return the same top-k for the same query (within ordering by score).

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Modify: `tests/search/test_tagged_kvdb.py`

### Steps

- [ ] **Step 1: Append the failing tests.**

```python
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
    # Seed identical content into two stores; one forced to brute force,
    # the other forced to HNSW filter. Top-k results should match.
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
    # Sanity: with threshold 0, even a 1-element filtered set goes through HNSW.
    db = TaggedKVDatabase(embedder=fake_embedder, brute_force_threshold=0)
    db.add("only", "v", tags=["t"])
    results = db.search("only", n=1, tags=["t"])
    assert results == [("v", pytest.approx(1.0, abs=1e-4))]
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "search"`
Expected: AttributeError — `TaggedKVDatabase.search` does not exist.

- [ ] **Step 3: Implement `search()` in `tagged_kvdb.py`.**

Add `numpy as np` import if not already present. Add this method to `TaggedKVDatabase`:

```python
    # ---- vector search --------------------------------------------------
    def search(
        self,
        phrase: str,
        n: int,
        tags: Iterable[str] = (),
    ) -> list[tuple[JSONValue, float]]:
        vec = self._encode(phrase)

        with self._lock.read():
            if n <= 0:
                return []

            allowed = self._intersect_tag_ids(tags)
            if allowed is not None and not allowed:
                return []

            live = (
                len(allowed) if allowed is not None
                else len(self._store.id_to_value)
            )
            if live == 0:
                return []
            k = min(n, live)

            use_brute = (
                allowed is not None
                and len(allowed) <= self._brute_force_threshold
            )

            if use_brute:
                ids = list(allowed)
                mat = np.asarray(self._index.get_items(ids), dtype=np.float32)
                # Cosine similarity: normalise both sides.
                mat_norms = np.linalg.norm(mat, axis=1) + 1e-12
                mat_n = mat / mat_norms[:, None]
                q_norm = np.linalg.norm(vec) + 1e-12
                q_n = vec / q_norm
                sims = mat_n @ q_n
                if k < len(ids):
                    top = np.argpartition(-sims, k - 1)[:k]
                else:
                    top = np.arange(len(ids))
                top = top[np.argsort(-sims[top])]
                return [
                    (self._store.id_to_value[ids[int(i)]], float(sims[int(i)]))
                    for i in top
                ]

            filt = None if allowed is None else (lambda i: i in allowed)
            self._index.set_num_threads(1)
            labels, distances = self._index.knn_query(
                vec.reshape(1, -1), k=k, filter=filt
            )
            out: list[tuple[JSONValue, float]] = []
            for idx, dist in zip(labels[0], distances[0]):
                idx = int(idx)
                if idx not in self._store.id_to_value:
                    continue
                out.append((self._store.id_to_value[idx], 1.0 - float(dist)))
            return out
```

- [ ] **Step 4: Run the search tests to verify they pass.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "search"`
Expected: all 8 search tests pass.

- [ ] **Step 5: Re-run the full suite.**

Run: `pytest tests/search -v`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add src/search/tagged_kvdb.py tests/search/test_tagged_kvdb.py
git commit -m "feat(search): TaggedKVDatabase.search with adaptive filter path"
```

---

## Task 5: `search_paged`

**Goal:** Thin paging wrapper around `search`, with `tags` threaded through. Mirrors `KVDatabase.search_paged`.

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Modify: `tests/search/test_tagged_kvdb.py`

### Steps

- [ ] **Step 1: Append the failing tests.**

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "search_paged"`
Expected: AttributeError — `search_paged` does not exist.

- [ ] **Step 3: Add `search_paged` to `TaggedKVDatabase`.**

```python
    def search_paged(
        self,
        phrase: str,
        page_size: int,
        max_pages: Optional[int] = None,
        tags: Iterable[str] = (),
    ) -> PagedList[tuple[JSONValue, float]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")

        with self._lock.read():
            allowed = self._intersect_tag_ids(tags)
            if allowed is not None and not allowed:
                return PagedList([], page_size)
            live = (
                len(allowed) if allowed is not None
                else len(self._store.id_to_value)
            )
            n = live if max_pages is None else min(live, page_size * max_pages)
            results = self.search(phrase, n, tags=tags)
            return PagedList(results, page_size)
```

- [ ] **Step 4: Run the paged tests.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "search_paged"`
Expected: all 3 paged tests pass.

- [ ] **Step 5: Re-run the full suite.**

Run: `pytest tests/search -v`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add src/search/tagged_kvdb.py tests/search/test_tagged_kvdb.py
git commit -m "feat(search): TaggedKVDatabase.search_paged"
```

---

## Task 6: Persistence (v2 store, derived `tag_to_ids` on load)

**Goal:** Wire up `_extra_save_data` / `_extra_load_data` so `id_to_tags` is persisted and `tag_to_ids` is rebuilt on load. Cross-version loads raise.

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Modify: `tests/search/test_tagged_kvdb.py`

### Steps

- [ ] **Step 1: Append the failing tests.**

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "save or load or roundtrip or rebuilt"`
Expected: round-trip test loses the tags (gets empty `frozenset()` back); version tests likely also fail because v1 stores happen to load silently today.

- [ ] **Step 3: Implement the save/load hooks in `TaggedKVDatabase`.**

```python
    # ---- persistence hooks ---------------------------------------------
    def _extra_save_data(self) -> dict:
        return {
            "id_to_tags": {
                str(id_): sorted(tags) for id_, tags in self._id_to_tags.items()
            },
        }

    def _extra_load_data(self, data: dict) -> None:
        raw = data.get("id_to_tags", {})
        self._id_to_tags = {
            int(k): frozenset(v) for k, v in raw.items()
        }
        self._tag_to_ids = {}
        for id_, tags in self._id_to_tags.items():
            for t in tags:
                self._tag_to_ids.setdefault(t, set()).add(id_)
```

- [ ] **Step 4: Run the persistence tests to verify they pass.**

Run: `pytest tests/search/test_tagged_kvdb.py -v -k "save or load or roundtrip or rebuilt"`
Expected: all 4 persistence tests pass.

- [ ] **Step 5: Re-run the full suite.**

Run: `pytest tests/search -v`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add src/search/tagged_kvdb.py tests/search/test_tagged_kvdb.py
git commit -m "feat(search): persist TaggedKVDatabase tags (v2 store)"
```

---

## Task 7: Concurrency smoke test

**Goal:** One smoke test confirming filtered search and `add` can interleave safely. No new production code.

**Files:**
- Modify: `tests/search/test_tagged_kvdb.py`

### Steps

- [ ] **Step 1: Append the failing-or-passing smoke test.**

```python
import threading


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
```

- [ ] **Step 2: Run the concurrency test.**

Run: `pytest tests/search/test_tagged_kvdb.py::test_concurrent_readers_and_writer -v`
Expected: passes (no exceptions, all 100 entries present).

- [ ] **Step 3: Run the entire repo test suite as a final gate.**

Run: `pytest -v`
Expected: all green across `tests/search` and any other test packages.

- [ ] **Step 4: Commit.**

```bash
git add tests/search/test_tagged_kvdb.py
git commit -m "test(search): TaggedKVDatabase concurrent reader/writer smoke test"
```

---

## Done

After Task 7:

- `TaggedKVDatabase` is fully implemented, persisted, tested, and re-exported.
- `KVDatabase` is unchanged in behaviour but now shares its base machinery with the tagged variant.
- No new dependencies were added.
