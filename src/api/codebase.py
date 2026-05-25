from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

import numpy as np

from library import (
    BuildError, CodeNode, FolderNode, Graph, Node, Tag, Test, TestStatus, new_node_id,
)

from api.errors import ApiError, ImplementationFailed, InvalidMove
from api.results import ImplementResult, RebuildReport, SearchPage, TagPage
from api.search_system import SearchSystem

_NO_VEC = np.zeros(0, dtype=float)


def _count_tests(tests: str) -> int:
    try:
        tree = ast.parse(tests)
    except SyntaxError:
        return 0
    return sum(
        1 for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("test_")
    )


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
        tags.add(f"@searchable:{str(node.searchable).lower()}")
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

    def ensure_built(self, node_ids: Iterable[str]) -> None:
        for nid in node_ids:
            self._graph.ensure_built(nid)

    def children_of(self, node_id) -> set[str]:
        return self._graph.children_of(node_id)

    def list_tags(self) -> set[str]:
        return self._search.list_tags()

    # ---- folder operations ----
    def _subtree_ids(self, node_id: str) -> set[str]:
        """All descendant ids of node_id (excludes node_id itself)."""
        out: set[str] = set()
        frontier = list(self._graph.children_of(node_id))
        while frontier:
            nid = frontier.pop()
            if nid in out:
                continue
            out.add(nid)
            frontier.extend(self._graph.children_of(nid))
        return out

    def _attach_to_parent(self, node_id: str, parent_id: str) -> None:
        parent = self._graph.get(parent_id)
        parent.children.add(node_id)
        self._graph.update_node(parent)

    def _detach_from_parent(self, node_id: str, parent_id: str) -> None:
        parent = self._graph.get(parent_id)
        parent.children.discard(node_id)
        self._graph.update_node(parent)

    def _check_capacity(self, parent_id: str, adding: int = 1) -> None:
        cap = self._graph.config.max_folder_children
        if cap <= 0:
            return
        if len(self._graph.children_of(parent_id)) + adding > cap:
            raise InvalidMove(parent_id, parent_id, "folder-full")

    def make_folder(self, name, *, parent_id=None, description="", tags=(),
                    searchable: bool = True) -> str:
        parent_id = parent_id or self._root_id
        if not isinstance(self._graph.get(parent_id), FolderNode):
            raise InvalidMove(parent_id, parent_id, "target-not-folder")
        self._check_capacity(parent_id)
        nid = new_node_id()
        folder = FolderNode(node_id=nid, name=name, description=description,
                            parent_id=parent_id, tags=self._tagset(tags),
                            searchable=searchable)
        self._graph.add_node(folder)
        self._attach_to_parent(nid, parent_id)
        self._index_node(folder)
        return nid

    def move(self, node_ids, new_parent_id) -> None:
        ids = list(dict.fromkeys([node_ids] if isinstance(node_ids, str) else node_ids))
        if not isinstance(self._graph.get(new_parent_id), FolderNode):
            raise InvalidMove(new_parent_id, new_parent_id, "target-not-folder")
        # validate every move before mutating anything (all-or-nothing)
        for nid in ids:
            if nid == self._root_id:
                raise InvalidMove(nid, new_parent_id, "move-root")
            if new_parent_id == nid or new_parent_id in self._subtree_ids(nid):
                raise InvalidMove(nid, new_parent_id, "into-own-subtree")
        existing = self._graph.children_of(new_parent_id)
        incoming = [nid for nid in ids if nid not in existing]
        self._check_capacity(new_parent_id, adding=len(incoming))
        for nid in ids:
            node = self._graph.get(nid)
            old_parent = node.parent_id
            if old_parent is not None:
                self._detach_from_parent(nid, old_parent)
            node.parent_id = new_parent_id
            self._graph.update_node(node)
            self._attach_to_parent(nid, new_parent_id)
            for sub in [nid, *self._subtree_ids(nid)]:
                self._search.update_tags(sub, self._composite_tags(self._graph.get(sub)))

    def rename(self, node_id, new_name) -> None:
        node = self._graph.get(node_id)
        node.name = new_name
        self._graph.update_node(node)
        self._index_node(node)

    def remove(self, node_id) -> None:
        node = self._graph.get(node_id)
        if isinstance(node, FolderNode) and node.children:
            raise ApiError(f"cannot remove non-empty folder {node_id}")
        if node.parent_id is not None:
            self._detach_from_parent(node_id, node.parent_id)
        self._graph.remove_node(node_id)
        self._search.remove_node(node_id)

    def define_abstraction(self, name, description, object_type, *,
                           parent_id=None, dependencies=(), tags=(),
                           searchable: bool = True) -> str:
        parent_id = parent_id or self._root_id
        if not isinstance(self._graph.get(parent_id), FolderNode):
            raise InvalidMove(parent_id, parent_id, "target-not-folder")
        self._check_capacity(parent_id)
        nid = new_node_id()
        node = CodeNode(node_id=nid, name=name, description=description,
                        parent_id=parent_id, tags=self._tagset(tags),
                        dependencies=set(dependencies), object_type=object_type, tests=[],
                        searchable=searchable)
        self._graph.add_node(node)
        self._attach_to_parent(nid, parent_id)
        self._index_node(node)
        return nid

    def add_method(self, name, description, **kw) -> str:
        return self.define_abstraction(name, description, "method", **kw)

    def add_class(self, name, description, **kw) -> str:
        return self.define_abstraction(name, description, "class", **kw)

    def add_executable(self, name, description, **kw) -> str:
        return self.define_abstraction(name, description, "executable", **kw)

    def _all_passing(self, node) -> bool:
        return bool(node.tests) and all(t.status == TestStatus.PASSING for t in node.tests)

    def dirty(self) -> set[str]:
        out = set()
        for nid in self._graph.iter_code_ids():
            node = self._graph.get(nid)
            if self._graph.is_build_stale(nid) or not self._all_passing(node):
                out.add(nid)
        return out

    def _topo(self, ids: set[str]) -> list[str]:
        ids = set(ids)
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(n: str) -> None:
            if n in visited:
                return
            visited.add(n)
            node = self._graph.get(n)
            for dep in getattr(node, "dependencies", set()):
                if dep in ids:
                    visit(dep)
            ordered.append(n)

        for n in ids:
            visit(n)
        return ordered

    def rebuild(self, node_id=None) -> RebuildReport:
        dirty = self.dirty()
        report = RebuildReport()
        if node_id is not None:
            sub = self._subtree_ids(node_id) | {node_id}
            code_in_sub = {n for n in sub if n in set(self._graph.iter_code_ids())}
            report.skipped = sorted(code_in_sub - dirty)
            dirty = dirty & code_in_sub
        for nid in self._topo(dirty):
            try:
                if self._graph.ensure_built(nid):
                    report.rebuilt.append(nid)
                self._graph.run_tests(nid)
            except BuildError:
                report.failed.append(nid)
                continue
            if self._all_passing(self._graph.get(nid)):
                report.passed.append(nid)
            else:
                report.failed.append(nid)
        return report

    def implement(self, node_id, code, tests) -> ImplementResult:
        node = self._graph.get(node_id)
        if not isinstance(node, CodeNode):
            raise ApiError(f"{node_id} is not a code node")
        floor = self._graph.config.min_tests_per_method
        if floor > 0:
            got = _count_tests(tests)
            if got < floor:
                raise ImplementationFailed(
                    node_id, results=[], detail=f"got {got} tests, need >= {floor}")
        results = self._graph.trial_run(node_id, code, tests)
        passing = bool(results) and all(r.status == TestStatus.PASSING for r in results)
        if not passing:
            self._graph.discard_trial(node_id)
            detail = next((r.detail for r in results if r.detail), None) or (
                "no tests defined" if not results else "tests failed")
            raise ImplementationFailed(node_id, results=results, detail=detail)
        node.tests = [Test(name=r.name, status=r.status) for r in results]
        self._graph.update_node(node, code=code, tests=tests)
        self._graph.ensure_built(node_id)
        return ImplementResult(node_id=node_id, results=results, all_passing=True)

    def set_searchable(self, node_id, value) -> None:
        node = self._graph.get(node_id)
        node.searchable = bool(value)
        self._graph.update_node(node)
        self._search.update_tags(node_id, self._composite_tags(node))

    def search(self, query, *, page=0, page_size=10, tags=(), folders=(),
               object_types=(), include_hidden=False) -> SearchPage:
        require_all = set() if include_hidden else {"@searchable:true"}
        return self._search.search_page(
            query, page=page, page_size=page_size, tags=set(tags),
            object_types=set(object_types), folders=set(folders), require_all=require_all)

    def search_tags(self, query, *, page=0, page_size=10) -> TagPage:
        return self._search.search_tags_page(query, page=page, page_size=page_size)
