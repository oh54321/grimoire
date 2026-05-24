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
        """Must be called under the write lock."""
        self._id_to_tags[id_] = tags
        for t in tags:
            self._tag_to_ids.setdefault(t, set()).add(id_)

    def _remove_id_from_tags(self, id_: int) -> None:
        """Must be called under the write lock."""
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
            return self._id_to_tags[id_]

    def all_tags(self) -> set[str]:
        with self._lock.read():
            return set(self._tag_to_ids.keys())
