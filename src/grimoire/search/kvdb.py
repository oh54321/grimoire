from __future__ import annotations

import json

import numpy as np

from grimoire.search._base import (
    JSONValue,
    STORE_VERSION,
    _VectorStoreBase,
)
from grimoire.search.pages import PagedList

__all__ = ["KVDatabase", "JSONValue"]


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
        max_pages: int | None = None,
    ) -> PagedList[tuple[JSONValue, float]]:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")

        with self._lock.read():
            live = len(self._store.id_to_value)
            n = live if max_pages is None else min(live, page_size * max_pages)
            results = self.search(phrase, n)
            return PagedList(results, page_size)
