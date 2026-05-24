from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import hnswlib
import numpy as np

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
        """Return extra JSON-serialisable keys to merge into the store file.

        Called under the read lock. Must not attempt to acquire the lock.
        """
        return {}

    def _extra_load_data(self, data: dict) -> None:
        """Consume subclass keys from the loaded JSON dict.

        Called under the write lock. Must not attempt to acquire the lock.
        """
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
