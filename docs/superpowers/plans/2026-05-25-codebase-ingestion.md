# Codebase Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add codebase ingestion to the MCP — clone an external git repo into an ephemeral sandbox, browse it read-only, and pull selected functions/classes into the library as test-gated nodes via the existing `define`/`implement` gate. (`integrate-mcp` = this, pointed at an MCP server's repo.)

**Architecture:** A `RepoStore` (`src/codebase_mcp/ingest.py`) does shallow ephemeral clones + read-only browse and executes nothing. `Workspace` gains ingestion methods that compose `RepoStore` reads with `Codebase.define`/`implement`. `server.py` binds new tools. No new authoritative state.

**Tech Stack:** Python 3.11+, pytest (`pythonpath=["src"]`), git CLI (`subprocess`), the core Codebase MCP.

**Spec:** `docs/superpowers/specs/2026-05-25-codebase-ingestion-design.md`

**Prerequisite:** the core Codebase MCP must be complete (its `McpConfig`, `Workspace` with `define`/`implement`/`search`/`run_scratch`, and `server.py` with `TOOL_NAMES`/`build_server`). Do not start until those exist.

**Conventions:** run a test with `python -m pytest <path>::<name> -v` from repo root. Tests build a local git repo fixture and clone it via a `file://` URL (no network). Use the existing `FakeEmbedder` (`tests/api/test_search_system.py`) when opening a `Workspace`.

---

## Task 1: `McpConfig` ingestion settings

**Files:**
- Modify: `src/codebase_mcp/config.py`
- Test: `tests/codebase_mcp/test_config.py`

- [ ] **Step 1: Write the failing test** — append to `tests/codebase_mcp/test_config.py`:

```python
def test_ingest_defaults_and_env(tmp_path):
    from codebase_mcp.config import McpConfig
    cfg = McpConfig.from_env(env={})
    assert cfg.clone_timeout == 120.0
    assert cfg.allow_clone is True
    assert str(cfg.ingest_root).endswith("haymanbot-ingest")

    cfg2 = McpConfig.from_env(env={
        "HAYMANBOT_INGEST_ROOT": "/tmp/ing",
        "HAYMANBOT_CLONE_TIMEOUT": "30",
        "HAYMANBOT_ALLOW_CLONE": "false",
    })
    assert str(cfg2.ingest_root) == "/tmp/ing"
    assert cfg2.clone_timeout == 30.0
    assert cfg2.allow_clone is False
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/codebase_mcp/test_config.py::test_ingest_defaults_and_env -v` (AttributeError: no `clone_timeout`).

- [ ] **Step 3: Implement** — add to the `McpConfig` dataclass fields:

```python
    ingest_root: Path = None  # set in from_env; see below
    clone_timeout: float = 120.0
    allow_clone: bool = True
```

In `from_env`, before the `return cls(...)`, compute the ingest root and flags:

```python
        import tempfile
        raw_ingest = env.get("HAYMANBOT_INGEST_ROOT")
        ingest_root = (Path(raw_ingest).expanduser() if raw_ingest
                       else Path(tempfile.gettempdir()) / "haymanbot-ingest")
        clone_timeout = float(env.get("HAYMANBOT_CLONE_TIMEOUT") or 120.0)
        allow_clone = (env.get("HAYMANBOT_ALLOW_CLONE", "true").lower() != "false")
```

and pass `ingest_root=ingest_root, clone_timeout=clone_timeout, allow_clone=allow_clone` into the `cls(...)` call. (If `ingest_root` as a non-default-after-default field causes a dataclass ordering error, give it `field(default=None)` and set it only in `from_env`; the direct constructor callers in tests always go through `from_env` or pass it explicitly.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_config.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/config.py tests/codebase_mcp/test_config.py
git commit -m "feat(ingest): McpConfig ingestion settings"
```

---

## Task 2: `RepoStore` — clone + read-only browse

**Files:**
- Create: `src/codebase_mcp/ingest.py`
- Test: `tests/codebase_mcp/test_ingest_repostore.py`

- [ ] **Step 1: Write the failing test** — create `tests/codebase_mcp/test_ingest_repostore.py`:

```python
import subprocess
from pathlib import Path
import pytest
from codebase_mcp.ingest import RepoStore


def _make_repo(tmp_path) -> str:
    src = tmp_path / "upstream"
    (src).mkdir()
    (src / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=src, check=True)
    return f"file://{src}"


def _store(tmp_path, **kw):
    return RepoStore(root=tmp_path / "ing", timeout=60.0, allow_clone=True, **kw)


def test_clone_and_browse(tmp_path):
    url = _make_repo(tmp_path)
    store = _store(tmp_path)
    repo_id, files = store.clone(url)
    assert "mod.py" in files
    assert "def add" in store.read(repo_id, "mod.py")
    assert any("mod.py" in f for f in store.tree(repo_id))


def test_drop_removes_clone(tmp_path):
    url = _make_repo(tmp_path)
    store = _store(tmp_path)
    repo_id, _ = store.clone(url)
    store.drop(repo_id)
    assert repo_id not in store.list_repos()


def test_clone_disabled(tmp_path):
    url = _make_repo(tmp_path)
    store = RepoStore(root=tmp_path / "ing", timeout=60.0, allow_clone=False)
    with pytest.raises(PermissionError):
        store.clone(url)
```

- [ ] **Step 2: Run, expect FAIL** — `ModuleNotFoundError: codebase_mcp.ingest`.

- [ ] **Step 3: Implement** — create `src/codebase_mcp/ingest.py`:

```python
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path


class RepoStore:
    """Ephemeral shallow git clones, read-only browse. Executes nothing from the repo."""

    def __init__(self, root: Path, timeout: float = 120.0, allow_clone: bool = True) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._allow_clone = allow_clone
        self._repos: dict[str, Path] = {}

    def clone(self, git_url: str, ref: str | None = None) -> tuple[str, list[str]]:
        if not self._allow_clone:
            raise PermissionError("cloning is disabled (allow_clone=False)")
        repo_id = uuid.uuid4().hex[:12]
        dest = self._root / repo_id
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = ["git", "clone", "--depth", "1", "--no-tags"]
        if ref:
            argv += ["--branch", ref]
        argv += [git_url, str(dest)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=self._timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"clone failed: {proc.stderr.strip().splitlines()[-1:] or proc.stderr}")
        self._repos[repo_id] = dest
        root_files = sorted(p.name for p in dest.iterdir() if p.name != ".git")
        return repo_id, root_files

    def _dir(self, repo_id: str) -> Path:
        d = self._repos.get(repo_id)
        if d is None:
            raise KeyError(f"unknown repo_id: {repo_id}")
        return d

    def _safe(self, repo_id: str, rel: str) -> Path:
        base = self._dir(repo_id).resolve()
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"path escapes repo: {rel}")
        return target

    def tree(self, repo_id: str, subpath: str = "") -> list[str]:
        base = self._dir(repo_id)
        start = self._safe(repo_id, subpath)
        out = []
        for p in sorted(start.rglob("*")):
            if ".git" in p.parts:
                continue
            out.append(str(p.relative_to(base)))
        return out

    def read(self, repo_id: str, path: str) -> str:
        return self._safe(repo_id, path).read_text()

    def list_repos(self) -> list[str]:
        return sorted(self._repos)

    def drop(self, repo_id: str) -> None:
        d = self._repos.pop(repo_id, None)
        if d and d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def drop_all(self) -> None:
        for rid in list(self._repos):
            self.drop(rid)
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_ingest_repostore.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/ingest.py tests/codebase_mcp/test_ingest_repostore.py
git commit -m "feat(ingest): RepoStore ephemeral clone + read-only browse"
```

---

## Task 3: Symbol extraction

**Files:**
- Modify: `src/codebase_mcp/ingest.py`
- Test: `tests/codebase_mcp/test_ingest_extract.py`

- [ ] **Step 1: Write the failing test** — create `tests/codebase_mcp/test_ingest_extract.py`:

```python
import pytest
from codebase_mcp.ingest import extract_symbol

SRC = (
    "import os\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "class Buf:\n"
    "    def push(self, x):\n"
    "        return x\n"
)


def test_extract_function():
    seg = extract_symbol(SRC, "add")
    assert seg == "def add(a, b):\n    return a + b"


def test_extract_class():
    seg = extract_symbol(SRC, "Buf")
    assert seg.startswith("class Buf:")
    assert "def push" in seg


def test_missing_symbol_raises():
    with pytest.raises(KeyError):
        extract_symbol(SRC, "nope")


def test_nested_symbol_not_returned():
    with pytest.raises(KeyError):
        extract_symbol(SRC, "push")   # nested method, not top-level
```

- [ ] **Step 2: Run, expect FAIL** — `ImportError: cannot import name 'extract_symbol'`.

- [ ] **Step 3: Implement** — add to `src/codebase_mcp/ingest.py` (module level, with `import ast` at top):

```python
import ast


def extract_symbol(source: str, symbol: str) -> str:
    """Return the exact source of a TOP-LEVEL function/class named `symbol`."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == symbol:
            seg = ast.get_source_segment(source, node)
            if seg is not None:
                return seg
    raise KeyError(symbol)
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_ingest_extract.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/ingest.py tests/codebase_mcp/test_ingest_extract.py
git commit -m "feat(ingest): top-level symbol source extraction"
```

---

## Task 4: `Workspace` ingestion methods

**Files:**
- Modify: `src/codebase_mcp/workspace.py`
- Test: `tests/codebase_mcp/test_workspace_ingest.py`

- [ ] **Step 1: Write the failing test** — create `tests/codebase_mcp/test_workspace_ingest.py`:

```python
import subprocess
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _make_repo(tmp_path):
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=src, check=True)
    return f"file://{src}"


def _ws(tmp_path):
    cfg = McpConfig(root=tmp_path / "cb", min_tests=0, max_folder_children=0,
                    ingest_root=tmp_path / "ing", clone_timeout=60.0, allow_clone=True)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_clone_browse_and_ingest(tmp_path):
    ws = _ws(tmp_path)
    info = ws.clone_repo(_make_repo(tmp_path))
    assert "mod.py" in info["root_files"]
    rid = info["repo_id"]
    assert "def add" in ws.repo_read(rid, "mod.py")["text"]

    res = ws.ingest_symbol(
        rid, "mod.py", "add",
        description="add two numbers",
        tests="def test_add():\n    assert add(1, 2) == 3\n",
    )
    assert res["ok"] is True
    nid = res["id"]
    assert nid in {h["id"] for h in ws.search("add two numbers")["hits"]}
    assert "return a + b" in ws.read_code(nid)["code"]


def test_ingest_requires_passing_tests(tmp_path):
    ws = _ws(tmp_path)
    rid = ws.clone_repo(_make_repo(tmp_path))["repo_id"]
    res = ws.ingest_symbol(rid, "mod.py", "add", description="adds",
                           tests="def test_add():\n    assert add(1, 2) == 99\n")
    assert res["ok"] is False           # failing test -> not ingested


def test_ingest_hidden_helper_stays_out_of_search(tmp_path):
    ws = _ws(tmp_path)
    rid = ws.clone_repo(_make_repo(tmp_path))["repo_id"]
    res = ws.ingest_symbol(rid, "mod.py", "add", description="narrow helper",
                           tests="def test_add():\n    assert add(1, 2) == 3\n",
                           searchable=False)
    assert res["ok"] is True
    nid = res["id"]
    assert nid not in {h["id"] for h in ws.search("narrow helper")["hits"]}        # hidden
    assert nid in {h["id"] for h in ws.search("narrow helper", include_hidden=True)["hits"]}


def test_drop_repo(tmp_path):
    ws = _ws(tmp_path)
    rid = ws.clone_repo(_make_repo(tmp_path))["repo_id"]
    assert rid in ws.list_repos()["repos"]
    ws.drop_repo(rid)
    assert rid not in ws.list_repos()["repos"]
```

- [ ] **Step 2: Run, expect FAIL** — `AttributeError: 'Workspace' object has no attribute 'clone_repo'`.

- [ ] **Step 3: Implement** — in `src/codebase_mcp/workspace.py`:

Import and construct a `RepoStore` in `Workspace.open` (and `__init__`). Add to imports:

```python
from codebase_mcp.ingest import RepoStore, extract_symbol
```

In `Workspace.__init__`, accept and store a `repos` param; in `open`, build it:

```python
        repos = RepoStore(root=config.ingest_root, timeout=config.clone_timeout,
                          allow_clone=config.allow_clone)
```

and pass it into `cls(...)`; store as `self._repos`.

Add methods:

```python
    # ---- ingestion ----
    def clone_repo(self, git_url: str, *, ref: str | None = None) -> dict:
        try:
            repo_id, files = self._repos.clone(git_url, ref=ref)
        except PermissionError as e:
            return {"ok": False, "reason": "clone-disabled", "detail": str(e)}
        except Exception as e:  # clone/network failure is a normal result
            return {"ok": False, "reason": "clone-failed", "detail": str(e)}
        return {"ok": True, "repo_id": repo_id, "root_files": files}

    def repo_tree(self, repo_id: str, *, subpath: str = "") -> dict:
        return {"repo_id": repo_id, "files": self._repos.tree(repo_id, subpath)}

    def repo_read(self, repo_id: str, path: str) -> dict:
        return {"repo_id": repo_id, "path": path, "text": self._repos.read(repo_id, path)}

    def list_repos(self) -> dict:
        return {"repos": self._repos.list_repos()}

    def drop_repo(self, repo_id: str) -> dict:
        self._repos.drop(repo_id)
        return {"ok": True}

    def ingest_symbol(self, repo_id: str, path: str, symbol: str, *,
                      description: str, tests: str, kind: str = "method",
                      name: str | None = None, parent: str | None = None,
                      dependencies: list[str] | None = None,
                      tags: list[str] | None = None, searchable: bool = True) -> dict:
        try:
            source = self._repos.read(repo_id, path)
            code = extract_symbol(source, symbol)
        except KeyError:
            return {"ok": False, "reason": "symbol-not-found",
                    "detail": f"{symbol!r} not a top-level def/class in {path}"}
        d = self.define(kind, name or symbol, description, parent=parent,
                        dependencies=dependencies, tags=tags, searchable=searchable)
        if not d.get("ok"):
            return d
        res = self.implement(d["id"], code + "\n", tests)
        if res.get("ok"):
            res["ingested_from"] = {"repo_id": repo_id, "path": path, "symbol": symbol}
        return res
```

(Note: `define` and `implement` here are the existing core `Workspace` methods — `define` accepts `searchable`, `implement` enforces the test gate and returns the structured result. If a `define`/`implement` failure leaves a stub node, that is the same behavior as a normal failed implement; no special handling needed.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_workspace_ingest.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/workspace.py tests/codebase_mcp/test_workspace_ingest.py
git commit -m "feat(ingest): Workspace clone/browse/ingest_symbol"
```

---

## Task 5: Register ingestion tools + guidance

**Files:**
- Modify: `src/codebase_mcp/server.py`
- Test: `tests/codebase_mcp/test_server.py`

- [ ] **Step 1: Write the failing test** — append to `tests/codebase_mcp/test_server.py`:

```python
def test_ingestion_tools_registered():
    from codebase_mcp.server import TOOL_NAMES
    for name in ("clone_repo", "repo_tree", "repo_read", "list_repos", "drop_repo", "ingest_symbol"):
        assert name in TOOL_NAMES
```

- [ ] **Step 2: Run, expect FAIL** — the names are absent from `TOOL_NAMES`.

- [ ] **Step 3: Implement** — in `src/codebase_mcp/server.py`, add an ingestion group to `TOOL_NAMES`:
`"clone_repo", "repo_tree", "repo_read", "list_repos", "drop_repo", "ingest_symbol"`.
Extend the server `instructions=` guidance: *"To grow the library from existing code, clone_repo a source, browse it with repo_tree/repo_read, then ingest_symbol the functions/classes worth keeping (you must supply passing tests). Be selective to avoid bloat: ingest only broadly-reusable symbols; ingest narrow helpers that exist only as dependencies with searchable=False so they stay out of search. Drop the repo when done."*
Also reflect the restraint in the `ingest_symbol` tool description (its docstring): note that `searchable=False` should be used for narrow helper symbols.

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_server.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/server.py tests/codebase_mcp/test_server.py
git commit -m "feat(ingest): register ingestion tools + guidance"
```

---

## Task 6: End-to-end ingestion integration test

**Files:**
- Test: `tests/codebase_mcp/test_ingest_integration.py`

- [ ] **Step 1: Write the test** — create `tests/codebase_mcp/test_ingest_integration.py`:

```python
import subprocess
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _make_repo(tmp_path):
    src = tmp_path / "upstream"
    src.mkdir()
    (src / "mathy.py").write_text("def triple(x):\n    return x * 3\n")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=src, check=True)
    return f"file://{src}"


def test_clone_ingest_search_scratch(tmp_path):
    cfg = McpConfig(root=tmp_path / "cb", min_tests=1, max_folder_children=7,
                    ingest_root=tmp_path / "ing", clone_timeout=60.0, allow_clone=True)
    ws = Workspace.open(cfg, embedder=FakeEmbedder())

    rid = ws.clone_repo(_make_repo(tmp_path))["repo_id"]
    res = ws.ingest_symbol(rid, "mathy.py", "triple", description="multiply by three",
                           tests="def test_t():\n    assert triple(4) == 12\n")
    assert res["ok"] is True
    nid = res["id"]
    assert nid in {h["id"] for h in ws.search("multiply by three")["hits"]}
    out = ws.run_scratch("print(triple(10))", deps=[nid])
    assert out["ok"] is True and "30" in out["stdout"]
    ws.drop_repo(rid)
```

- [ ] **Step 2: Run, expect PASS** — `python -m pytest tests/codebase_mcp/test_ingest_integration.py -v`.

- [ ] **Step 3: Full suite** — `python -m pytest -q` (expect all pass).

- [ ] **Step 4: Commit**

```bash
git add tests/codebase_mcp/test_ingest_integration.py
git commit -m "test(ingest): end-to-end clone->ingest->search->scratch"
```

---

## Self-review notes

- **Spec coverage:** config (Task 1) ✓; RepoStore clone/browse/drop + allow_clone (Task 2) ✓; symbol extraction top-level only (Task 3) ✓; Workspace clone/browse/ingest_symbol with required tests (Task 4) ✓; tools + guidance (Task 5) ✓; integration (Task 6) ✓.
- **Security:** clone is shallow/tagless/ephemeral and never enters the node store; path traversal is blocked in `RepoStore._safe`; the only execution is `implement`'s sandboxed trial-run on the one chosen symbol; `allow_clone=False` disables fetching. Residual test-worker isolation hardening is explicitly future work.
- **Dependency on core:** every Workspace method here calls existing core methods (`define`, `implement`, `search`, `run_scratch`); do not start before the core MCP is merged.
- **No magic in ingest:** ingestion never auto-runs or bulk-imports; Claude curates symbol-by-symbol and must supply tests.
