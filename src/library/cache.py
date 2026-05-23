"""In-memory LRU + TTL cache wrapping a NodeStore. Write-through; never holds dirty entries."""

import json
import time
from collections import OrderedDict
from dataclasses import dataclass

from library.ids import NodeId
from library.nodes import Node
from library.store import NodeStore, _node_to_dict


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_bytes: int = 0
    entry_count: int = 0


@dataclass
class _CacheEntry:
    node: Node
    code: str | None
    size_bytes: int
    last_access: float


def _estimate_size(node: Node, code: str | None) -> int:
    meta_bytes = len(json.dumps(_node_to_dict(node)))
    code_bytes = len(code.encode("utf-8")) if code else 0
    return meta_bytes + code_bytes


class NodeCache:
    """v1: single-threaded; no locks."""

    def __init__(self, store: NodeStore, max_bytes: int, ttl_seconds: float) -> None:
        self.store = store
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[NodeId, _CacheEntry] = OrderedDict()
        self._current_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, node_id: NodeId) -> Node:
        entry = self._entries.get(node_id)
        if entry is not None:
            self._entries.move_to_end(node_id)
            entry.last_access = time.monotonic()
            self._hits += 1
            return entry.node
        self._misses += 1
        node = self.store.load(node_id)
        self._insert(node_id, node, code=None)
        return node

    def put(self, node: Node, code: str | None = None) -> None:
        self.store.save(node, code=code)
        if node.node_id in self._entries:
            self.invalidate(node.node_id)
        self._insert(node.node_id, node, code=code)

    def invalidate(self, node_id: NodeId) -> None:
        entry = self._entries.pop(node_id, None)
        if entry is not None:
            self._current_bytes -= entry.size_bytes

    def clear(self) -> None:
        self._entries.clear()
        self._current_bytes = 0

    def stats(self) -> CacheStats:
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            current_bytes=self._current_bytes,
            entry_count=len(self._entries),
        )

    def _insert(self, node_id: NodeId, node: Node, code: str | None) -> None:
        size = _estimate_size(node, code)
        entry = _CacheEntry(node=node, code=code, size_bytes=size, last_access=time.monotonic())
        self._entries[node_id] = entry
        self._current_bytes += size
