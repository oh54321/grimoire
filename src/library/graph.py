"""Public facade for the library. Owns the in-memory index plus store/cache/builder/runner."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.builder import Builder
from library.cache import NodeCache
from library.config import LibraryConfig
from library.errors import BuildError, DuplicateNodeId, NodeNotFound
from library.ids import NodeId
from library.nodes import CodeNode, FolderNode, Node
from library.runner import Runner, TestResult
from library.store import NodeStore


@dataclass
class _Index:
    parent: dict[NodeId, NodeId | None] = field(default_factory=dict)
    children: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    tags: dict[str, set[NodeId]] = field(default_factory=dict)
    dependents: dict[NodeId, set[NodeId]] = field(default_factory=dict)

    def add(self, node: Node) -> None:
        nid = node.node_id
        self.parent[nid] = node.parent_id
        self.children.setdefault(nid, set())
        for tag in node.tags:
            self.tags.setdefault(tag.text, set()).add(nid)
        if isinstance(node, FolderNode):
            self.children[nid] = set(node.children)
        elif isinstance(node, CodeNode):
            for dep_id in node.dependencies:
                self.dependents.setdefault(dep_id, set()).add(nid)

    def remove(self, node: Node) -> None:
        nid = node.node_id
        self.parent.pop(nid, None)
        # remove from parent's children set
        if node.parent_id and node.parent_id in self.children:
            self.children[node.parent_id].discard(nid)
        self.children.pop(nid, None)
        # remove from tags
        for tag in node.tags:
            s = self.tags.get(tag.text)
            if s is not None:
                s.discard(nid)
                if not s:
                    del self.tags[tag.text]
        # remove as a dependent everywhere
        if isinstance(node, CodeNode):
            for dep_id in node.dependencies:
                s = self.dependents.get(dep_id)
                if s is not None:
                    s.discard(nid)
                    if not s:
                        del self.dependents[dep_id]
        # remove its own dependents entry (now-removed node had dependents pointing at it; those are orphans)
        self.dependents.pop(nid, None)


class Graph:
    def __init__(
        self,
        store: NodeStore,
        cache: NodeCache,
        builder: Builder,
        runner: Runner,
        config: LibraryConfig,
    ) -> None:
        self._store = store
        self._cache = cache
        self._builder = builder
        self._runner = runner
        self._config = config
        self._index = _Index()
        self._rebuild_index()

    @property
    def config(self) -> LibraryConfig:
        return self._config

    @classmethod
    def open(cls, root: Path, **config_overrides: Any) -> "Graph":
        cfg = LibraryConfig.load(root)
        if config_overrides:
            from dataclasses import replace
            cfg = replace(cfg, **config_overrides)
        cfg.save()
        store = NodeStore(cfg)
        cache = NodeCache(store, max_bytes=cfg.max_cache_mb * 1024 * 1024, ttl_seconds=cfg.ttl_seconds)
        builder = Builder(store, cache, build_root=root / "build")
        runner = Runner(
            build_root=root / "build",
            use_worker=cfg.use_test_worker,
            timeout=cfg.test_timeout_seconds,
        )
        return cls(store, cache, builder, runner, cfg)

    # ----- navigation -----

    def children_of(self, node_id: NodeId) -> set[NodeId]:
        return set(self._index.children.get(node_id, set()))

    def parent_of(self, node_id: NodeId) -> NodeId | None:
        return self._index.parent.get(node_id)

    def find_by_tag(self, tag_text: str) -> set[NodeId]:
        return set(self._index.tags.get(tag_text, set()))

    def dependencies_of(self, node_id: NodeId) -> set[NodeId]:
        node = self._cache.get(node_id)
        return set(node.dependencies) if isinstance(node, CodeNode) else set()

    def dependents_of(self, node_id: NodeId) -> set[NodeId]:
        return set(self._index.dependents.get(node_id, set()))

    def iter_ids(self):
        return self._store.iter_ids()

    def iter_code_ids(self):
        for nid in self._store.iter_ids():
            if isinstance(self._cache.get(nid), CodeNode):
                yield nid

    def is_build_stale(self, node_id: NodeId) -> bool:
        return self._builder.is_stale_with_deps(node_id)

    # ----- node access -----

    def get(self, node_id: NodeId) -> Node:
        return self._cache.get(node_id)

    def get_code(self, node_id: NodeId) -> str:
        return self._cache.get_code(node_id)

    def get_tests(self, node_id: NodeId) -> str:
        return self._store.load_tests(node_id)

    # ----- mutation -----

    def add_node(self, node: Node, code: str | None = None, tests: str | None = None) -> NodeId:
        if self._store.exists(node.node_id):
            raise DuplicateNodeId(node.node_id)
        self._store.save(node, code=code, tests=tests)
        self._cache.invalidate(node.node_id)
        self._index.add(node)
        return node.node_id

    def update_node(self, node: Node, code: str | None = None, tests: str | None = None) -> None:
        if not self._store.exists(node.node_id):
            raise NodeNotFound(node.node_id)
        old = self._store.load(node.node_id)
        self._store.save(node, code=code, tests=tests)
        self._cache.invalidate(node.node_id)
        self._index.remove(old)
        self._index.add(node)
        if code is not None:
            self._builder.invalidate(node.node_id)

    def remove_node(self, node_id: NodeId) -> None:
        node = self._store.load(node_id)
        self._store.delete(node_id)
        self._cache.invalidate(node_id)
        self._index.remove(node)
        self._builder.remove(node_id)

    # ----- build + run -----

    def ensure_built(self, node_id: NodeId) -> bool:
        return self._builder.ensure_built(node_id)

    def run_tests(self, node_id: NodeId) -> list[TestResult]:
        self.ensure_built(node_id)
        results = self._runner.run_tests(node_id)
        # Fold results back into the node's Test list and persist
        node = self._cache.get(node_id)
        if isinstance(node, CodeNode):
            by_name = {r.name: r for r in results}
            for t in node.tests:
                r = by_name.get(t.name)
                if r is not None:
                    t.status = r.status
            # Persist updated statuses without rewriting code/tests
            self._store.save(node)
            self._cache.invalidate(node_id)
        return results

    def trial_run(self, node_id: NodeId, code: str, tests: str) -> list[TestResult]:
        """Build + test candidate code/tests in the build scratch area without
        committing to the store. Returns the per-test results."""
        node = self._cache.get(node_id)
        if not isinstance(node, CodeNode):
            raise BuildError(node_id, "only CodeNodes can be implemented")
        for dep_id in node.dependencies:
            self.ensure_built(dep_id)
        self._builder.build_trial(node_id, code, tests, node.dependencies)
        return self._runner.run_tests(node_id)

    def discard_trial(self, node_id: NodeId) -> None:
        """Undo a failed trial. Restores the canonical build if the node has
        committed code; otherwise removes the scratch build files."""
        if self._store.load_code(node_id):
            self._builder.invalidate(node_id)
            self.ensure_built(node_id)
        else:
            self._builder.remove(node_id)

    # ----- index rebuild -----

    def _rebuild_index(self) -> None:
        self._index = _Index()
        for nid in self._store.iter_ids():
            try:
                node = self._store.load(nid)
            except Exception:
                continue
            self._index.add(node)
