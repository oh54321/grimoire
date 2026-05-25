from __future__ import annotations

from pathlib import Path

from search.kvdb import KVDatabase
from search.tagged_kvdb import TaggedKVDatabase

from api.results import SearchHit, TagHit


class SearchSystem:
    """Owns the node vector index (keyed by node_id, embedding the description,
    composite tags) and the real-tag vocabulary index. Graph-agnostic: filters
    arrive as composite tags / sets, never as tree lookups."""

    def __init__(self, nodes: TaggedKVDatabase, tags: KVDatabase) -> None:
        self._nodes = nodes
        self._tags = tags

    @classmethod
    def open(cls, index_root, embedder=None) -> "SearchSystem":
        index_root = Path(index_root)
        if embedder is None:
            from search.embedder import VectorConverter
            embedder = VectorConverter()
        nodes = TaggedKVDatabase(path=index_root / "nodes", embedder=embedder)
        tags = KVDatabase(path=index_root / "tags", embedder=embedder)
        return cls(nodes, tags)

    # ---- mutation (Codebase calls these in lockstep with Graph) ----
    def index_node(self, node_id: str, name: str, description: str, kind: str,
                   tags: set[str]) -> None:
        value = {"node_id": node_id, "name": name, "kind": kind, "description": description}
        self._nodes.add(description, value, tags=tags, key=node_id)

    def remove_node(self, node_id: str) -> None:
        self._nodes.delete(node_id)

    def update_tags(self, node_id: str, tags: set[str]) -> None:
        self._nodes.update_tags(node_id, tags)

    def index_tags(self, real_tags: set[str]) -> None:
        for t in real_tags:
            if t not in self._tags:
                self._tags.add(t, t)

    # ---- introspection ----
    def list_tags(self) -> set[str]:
        return {t for t in self._nodes.all_tags() if not t.startswith("@")}

    def is_empty(self) -> bool:
        return len(self._nodes) == 0

    def reindex(self, entries) -> None:
        # entries: iterable of (node_id, name, description, kind, composite_tags)
        real = set()
        for node_id, name, description, kind, tags in entries:
            self.index_node(node_id, name, description, kind, tags)
            real |= {t for t in tags if not t.startswith("@")}
        self.index_tags(real)

    # ---- query helpers ----
    def _any_groups(self, object_types: set[str], folders: set[str]):
        groups = []
        if folders:
            groups.append({f"@in:{f}" for f in folders})
        if object_types:
            groups.append({f"@kind:{t}" for t in object_types})
        return groups

    @staticmethod
    def _hit(value, score) -> SearchHit:
        return SearchHit(value["node_id"], value["name"], value["kind"],
                         value["description"], score)

    def search(self, query: str, *, n: int = 10, tags: set[str] = frozenset(),
               object_types: set[str] = frozenset(),
               folders: set[str] = frozenset()) -> list[SearchHit]:
        raw = self._nodes.search_filtered(
            query, n, all_tags=set(tags),
            any_groups=self._any_groups(set(object_types), set(folders)),
        )
        return [self._hit(v, s) for v, s in raw]

    def search_tags(self, query: str, *, n: int = 10) -> list[TagHit]:
        return [TagHit(v, s) for v, s in self._tags.search(query, n)]

    def save(self) -> None:
        self._nodes.save()
        self._tags.save()
