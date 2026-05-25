from __future__ import annotations

from pathlib import Path

from api.codebase import Codebase
from library import FolderNode, Node

from codebase_mcp.config import McpConfig
from codebase_mcp.scratch import ScratchRunner


class Workspace:
    """Transport-agnostic core. Owns one Codebase + a ScratchRunner and renders
    JSON-able results. Every MCP tool calls a method here."""

    def __init__(self, cb: Codebase, scratch: ScratchRunner, config: McpConfig) -> None:
        self._cb = cb
        self._scratch = scratch
        self._config = config

    @classmethod
    def open(cls, config: McpConfig | None = None, *, embedder=None) -> "Workspace":
        config = config or McpConfig.from_env()
        cb = Codebase.open(
            config.root, embedder=embedder,
            min_tests_per_method=config.min_tests,
            max_folder_children=config.max_folder_children,
        )
        scratch = ScratchRunner(Path(config.root), timeout=config.scratch_timeout)
        return cls(cb, scratch, config)

    # ---- helpers ----
    @staticmethod
    def _kind(node: Node) -> str:
        return "folder" if isinstance(node, FolderNode) else node.object_type

    def _stub(self, nid: str) -> dict:
        node = self._cb.load(nid)
        return {"id": nid, "name": node.name, "kind": self._kind(node)}

    # ---- search ----
    def search(self, query: str, *, tags: list[str] | None = None,
               object_types: list[str] | None = None,
               folders: list[str] | None = None, page: int = 0,
               include_hidden: bool = False) -> dict:
        pg = self._cb.search(query, page=page, tags=tuple(tags or ()),
                             object_types=tuple(object_types or ()),
                             folders=tuple(folders or ()), include_hidden=include_hidden)
        return {
            "query": pg.query, "page": pg.page, "num_pages": pg.num_pages, "total": pg.total,
            "hits": [{"id": h.node_id, "kind": h.kind, "name": h.name,
                      "description": h.description, "score": round(h.score, 4)}
                     for h in pg.hits],
        }

    def search_tags(self, query: str, *, page: int = 0) -> dict:
        pg = self._cb.search_tags(query, page=page)
        return {"query": pg.query, "page": pg.page, "num_pages": pg.num_pages,
                "total": pg.total, "hits": [{"tag": h.tag, "score": round(h.score, 4)}
                                            for h in pg.hits]}

    def list_tags(self) -> list[str]:
        return sorted(self._cb.list_tags())

    def discover(self, query: str, *, page: int = 0) -> dict:
        base = self.search(query, page=page)
        hits = base["hits"]
        tag_pg = self._cb.search_tags(query)
        folder_hits = self.search(query, object_types=["folder"], include_hidden=True)["hits"]
        return {
            "hits": hits,
            "candidate_tags": [{"tag": t.tag, "score": round(t.score, 4)} for t in tag_pg.hits],
            "candidate_folders": [{"id": h["id"], "name": h["name"], "score": h["score"]}
                                  for h in folder_hits],
            "object_types_present": sorted({h["kind"] for h in hits}),
            "hint": ("If hits look weak, call search(query, tags=[...], folders=[...], "
                     "object_types=[...]) with filters chosen from candidate_tags/"
                     "candidate_folders. Tag/folder/type filters are OR (match any)."),
        }

    # ---- read ----
    def view(self, node_id: str) -> dict:
        node = self._cb.load(node_id)
        if isinstance(node, FolderNode):
            return {"id": node_id, "kind": "folder", "name": node.name,
                    "description": node.description, "searchable": node.searchable,
                    "tags": sorted(t.text for t in node.tags),
                    "children": [self._stub(c) for c in sorted(self._cb.children_of(node_id))]}
        code = self._cb.load_code(node_id)
        signature = next((ln for ln in code.splitlines() if ln.strip()), "") if code else ""
        return {
            "id": node_id, "kind": node.object_type, "name": node.name,
            "description": node.description, "searchable": node.searchable,
            "dependencies": [{"id": d, "name": self._cb.load(d).name}
                             for d in sorted(node.dependencies)],
            "tags": sorted(t.text for t in node.tags),
            "tests": [{"name": t.name, "status": t.status.value} for t in node.tests],
            "signature": signature, "has_code": bool(code),
        }

    def read_code(self, node_id: str) -> dict:
        return {"id": node_id, "code": self._cb.load_code(node_id)}

    def read_tests(self, node_id: str) -> dict:
        return {"id": node_id, "tests": self._cb.load_tests(node_id)}

    def children(self, folder_id: str | None = None) -> list[dict]:
        fid = folder_id or self._cb.root_id
        return [self._stub(c) for c in sorted(self._cb.children_of(fid))]

    def tree(self, folder_id: str | None = None) -> dict:
        fid = folder_id or self._cb.root_id

        def build(nid: str) -> dict:
            node = self._cb.load(nid)
            d = {"id": nid, "name": node.name, "kind": self._kind(node)}
            if isinstance(node, FolderNode):
                d["children"] = [build(c) for c in sorted(self._cb.children_of(nid))]
            return d

        return build(fid)
