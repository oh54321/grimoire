from __future__ import annotations

from pathlib import Path

from api.codebase import Codebase
from api.errors import ApiError, ImplementationFailed, InvalidMove
from library import BuildError, FolderNode, Node

from codebase_mcp.config import McpConfig
from codebase_mcp.ingest.sandbox import FetchError, Sandbox
from codebase_mcp.ingest.survey import survey as _survey_fn, read_symbol as _read_symbol
from codebase_mcp.scratch import ScratchRunner

_KINDS = {"class", "method", "executable"}


class Workspace:
    """Transport-agnostic core. Owns one Codebase + a ScratchRunner and renders
    JSON-able results. Every MCP tool calls a method here."""

    def __init__(self, cb: Codebase, scratch: ScratchRunner, config: McpConfig,
                 sandbox: Sandbox) -> None:
        self._cb = cb
        self._scratch = scratch
        self._config = config
        self._sandbox = sandbox

    @classmethod
    def open(cls, config: McpConfig | None = None, *, embedder=None) -> "Workspace":
        config = config or McpConfig.from_env()
        cb = Codebase.open(
            config.root, embedder=embedder,
            min_tests_per_method=config.min_tests,
            max_folder_children=config.max_folder_children,
        )
        scratch = ScratchRunner(Path(config.root), timeout=config.scratch_timeout)
        sandbox = Sandbox(config.ingest_root, timeout=config.ingest_timeout)
        return cls(cb, scratch, config, sandbox)

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
               include_hidden: bool = False,
               is_tool: bool | None = None) -> dict:
        pg = self._cb.search(query, page=page, tags=tuple(tags or ()),
                             object_types=tuple(object_types or ()),
                             folders=tuple(folders or ()), include_hidden=include_hidden,
                             is_tool=is_tool)
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
        # include_hidden=True is intentional: surface folders as navigation hints even if hidden
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
            "is_tool": node.is_tool,
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

    # ---- create + build ----
    def define(self, kind: str, name: str, description: str, *, parent: str | None = None,
               dependencies: list[str] | None = None, tags: list[str] | None = None,
               searchable: bool = True, is_tool: bool = True) -> dict:
        if kind not in _KINDS:
            return {"ok": False, "reason": "bad-kind",
                    "detail": f"kind must be one of {sorted(_KINDS)}"}
        try:
            nid = self._cb.define_abstraction(
                name, description, kind, parent_id=parent,
                dependencies=tuple(dependencies or ()), tags=tuple(tags or ()),
                searchable=searchable, is_tool=is_tool)
        except InvalidMove as e:
            return self._invalid_move(e)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True, "id": nid}

    def implement(self, node_id: str, code: str, tests: str) -> dict:
        try:
            res = self._cb.implement(node_id, code, tests)
        except ImplementationFailed as e:
            return {"ok": False, "reason": "tests-failed", "detail": e.detail,
                    "required_tests": self._config.min_tests,
                    "failures": [{"name": r.name, "detail": r.detail}
                                 for r in e.results if r.status.name != "PASSING"]}
        except BuildError as e:
            return {"ok": False, "reason": "build-error", "detail": str(e)}
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True, "id": res.node_id,
                "tests": [{"name": r.name, "status": r.status.value} for r in res.results]}

    def dirty(self) -> dict:
        return {"nodes": [self._stub(nid) for nid in sorted(self._cb.dirty())]}

    def rebuild(self, node_id: str | None = None) -> dict:
        rep = self._cb.rebuild(node_id)
        return {"rebuilt": rep.rebuilt, "passed": rep.passed,
                "failed": rep.failed, "skipped": rep.skipped}

    def _invalid_move(self, e: InvalidMove) -> dict:
        if e.reason == "folder-full":
            cap = self._config.max_folder_children
            return {"ok": False, "reason": "folder-full", "folder_id": e.node_id, "cap": cap,
                    "hint": (f"folder is full (cap {cap}). Create a subfolder with make_folder "
                             "and move() related nodes into it, or move some children out, then retry.")}
        return {"ok": False, "reason": e.reason, "node_id": e.node_id, "target_id": e.target_id}

    # ---- refactor ----
    def make_folder(self, name: str, *, parent: str | None = None, description: str = "",
                    tags: list[str] | None = None, searchable: bool = True) -> dict:
        try:
            nid = self._cb.make_folder(name, parent_id=parent, description=description,
                                       tags=tuple(tags or ()), searchable=searchable)
        except InvalidMove as e:
            return self._invalid_move(e)
        return {"ok": True, "id": nid}

    def move(self, node_ids: "str | list[str]", new_parent: str) -> dict:
        try:
            self._cb.move(node_ids, new_parent)
        except InvalidMove as e:
            return self._invalid_move(e)
        return {"ok": True}

    def rename(self, node_id: str, new_name: str) -> dict:
        try:
            self._cb.rename(node_id, new_name)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True}

    def remove(self, node_id: str) -> dict:
        try:
            self._cb.remove(node_id)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True}

    def hide(self, node_id: str) -> dict:
        self._cb.set_searchable(node_id, False)
        return {"ok": True, "id": node_id, "searchable": False}

    def show(self, node_id: str) -> dict:
        self._cb.set_searchable(node_id, True)
        return {"ok": True, "id": node_id, "searchable": True}

    def mark_tool(self, node_id: str) -> dict:
        try:
            self._cb.set_is_tool(node_id, True)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True, "id": node_id, "is_tool": True}

    def mark_helper(self, node_id: str) -> dict:
        try:
            self._cb.set_is_tool(node_id, False)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True, "id": node_id, "is_tool": False}

    def health(self) -> dict:
        cap = self._config.max_folder_children
        over: list[dict] = []

        def walk(nid: str) -> None:
            node = self._cb.load(nid)
            if isinstance(node, FolderNode):
                n = len(self._cb.children_of(nid))
                if cap > 0 and n >= cap:
                    over.append({"id": nid, "name": node.name, "children": n, "cap": cap})
                for c in self._cb.children_of(nid):
                    walk(c)

        walk(self._cb.root_id)
        return {"cap": cap, "over": over}

    # ---- scratch ----
    def run_scratch(self, code: str, *, deps: list[str] | None = None) -> dict:
        deps = list(deps or ())
        try:
            self._cb.ensure_built(deps)
        except BuildError as e:
            return {"ok": False, "reason": "build-error", "detail": str(e)}
        import_lines = tuple(f"from build.{d} import {self._cb.load(d).name}" for d in deps)
        r = self._scratch.run(code, import_lines)
        return {"ok": r.exit_code == 0 and not r.timed_out, "exit_code": r.exit_code,
                "timed_out": r.timed_out, "stdout": r.stdout, "stderr": r.stderr}

    # ---- ingest (read-only source browse) ----
    def fetch_source(self, source: str, *, ref: str | None = None) -> dict:
        try:
            f = self._sandbox.fetch(source, ref=ref)
        except FetchError as e:
            return {"ok": False, "reason": "fetch-failed", "detail": str(e)}
        note = None if f.file_count else "no Python files found; v1 ingests Python only"
        return {"ok": True, "session": f.session, "file_count": f.file_count,
                "looks_like_mcp": f.looks_like_mcp, "top_modules": list(f.top_modules),
                "note": note}

    def survey_source(self, session: str, *, path: str | None = None) -> dict:
        root = self._sandbox.path(session)
        if not root.exists():
            return {"ok": False, "reason": "no-session", "detail": session}
        try:
            symbols, skipped = _survey_fn(root, sub=path)
        except (OSError, ValueError) as e:
            return {"ok": False, "reason": "survey-failed", "detail": str(e)}
        return {"ok": True, "symbols": [
            {"module": s.module, "qualname": s.qualname, "kind": s.kind,
             "signature": s.signature, "doc": s.doc_first_line, "mcp_tool": s.mcp_tool}
            for s in symbols], "skipped": skipped}

    def read_source(self, session: str, path: str, *, symbol: str | None = None) -> dict:
        root = self._sandbox.path(session)
        if not root.exists():
            return {"ok": False, "reason": "no-session", "detail": session}
        try:
            code = _read_symbol(root, path, symbol)
        except (OSError, KeyError) as e:
            return {"ok": False, "reason": "not-found", "detail": str(e)}
        return {"ok": True, "code": code}

    def discard_source(self, session: str) -> dict:
        removed = self._sandbox.discard(session)
        return {"ok": True, "removed": removed}
