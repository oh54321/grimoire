from __future__ import annotations

from pathlib import Path

import numpy as np

from library import (
    BuildError, CodeNode, FolderNode, Graph, Node, Tag, Test, TestStatus, new_node_id,
)

from api.errors import ApiError, ImplementationFailed, InvalidMove
from api.results import ImplementResult, RebuildReport, SearchPage, TagPage
from api.search_system import SearchSystem

_NO_VEC = np.zeros(0, dtype=float)


class Codebase:
    def __init__(self, graph: Graph, search: SearchSystem, root: Path) -> None:
        self._graph = graph
        self._search = search
        self._root = Path(root)
        self._root_id: str | None = None

    @classmethod
    def open(cls, root, *, embedder=None, **config_overrides) -> "Codebase":
        root = Path(root)
        graph = Graph.open(root, **config_overrides)
        search = SearchSystem.open(root / "index", embedder=embedder)
        cb = cls(graph, search, root)
        cb._reindex_if_empty()
        cb._ensure_root()
        return cb

    @property
    def root_id(self) -> str:
        assert self._root_id is not None
        return self._root_id

    # ---- helpers ----
    def _kind(self, node: Node) -> str:
        return "folder" if isinstance(node, FolderNode) else node.object_type

    def _ancestors(self, node_id: str) -> list[str]:
        out = []
        p = self._graph.parent_of(node_id)
        while p is not None:
            out.append(p)
            p = self._graph.parent_of(p)
        return out

    def _composite_tags(self, node: Node) -> set[str]:
        tags = {t.text for t in node.tags}
        tags.add(f"@kind:{self._kind(node)}")
        for anc in self._ancestors(node.node_id):
            tags.add(f"@in:{anc}")
        return tags

    def _index_node(self, node: Node) -> None:
        for t in node.tags:
            if t.text.startswith("@"):
                raise ApiError(f"tag may not start with '@': {t.text!r}")
        self._search.index_node(node.node_id, node.name, node.description,
                                self._kind(node), self._composite_tags(node))
        self._search.index_tags({t.text for t in node.tags})

    def _tagset(self, tags) -> set[Tag]:
        return {Tag(text=t, v=_NO_VEC) for t in tags}

    def _reindex_if_empty(self) -> None:
        if not self._search.is_empty():
            return
        ids = list(self._graph.iter_ids())
        if not ids:
            return
        entries = []
        for nid in ids:
            node = self._graph.get(nid)
            entries.append((nid, node.name, node.description, self._kind(node),
                            self._composite_tags(node)))
        self._search.reindex(entries)

    def _ensure_root(self) -> None:
        roots = [nid for nid in self._graph.iter_ids()
                 if isinstance(self._graph.get(nid), FolderNode)
                 and self._graph.parent_of(nid) is None]
        if len(roots) > 1:
            raise ApiError(f"multiple root folders: {roots}")
        if roots:
            self._root_id = roots[0]
            return
        rid = new_node_id()
        root = FolderNode(node_id=rid, name="root", description="Codebase root.", parent_id=None)
        self._graph.add_node(root)
        self._index_node(root)
        self._root_id = rid

    # ---- access ----
    def load(self, node_id) -> Node:
        return self._graph.get(node_id)

    def load_code(self, node_id) -> str:
        return self._graph.get_code(node_id)

    def load_tests(self, node_id) -> str:
        return self._graph.get_tests(node_id)

    def children_of(self, node_id) -> set[str]:
        return self._graph.children_of(node_id)

    def list_tags(self) -> set[str]:
        return self._search.list_tags()
