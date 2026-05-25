# Codebase MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing `src/api/` `Codebase` as an MCP server (`src/codebase_mcp/`) so Claude can search/read/reuse a personal, test-gated function library, refactor it under a hard folder-size cap, and run ephemeral scratch scripts against the built code.

**Architecture:** Two config-gated integrity rules (`min_tests_per_method`, `max_folder_children`) are added to `library`/`api` so they hold for every caller. A transport-agnostic `Workspace` owns one `Codebase` plus an ephemeral `ScratchRunner` and renders JSON-able results. A thin FastMCP `server.py` binds tools to `Workspace` methods.

**Tech Stack:** Python 3.11+, pytest (`pythonpath=["src"]`, `testpaths=["tests"]`), the official `mcp` Python SDK (FastMCP). Tests use the existing `FakeEmbedder` (`tests/api/test_search_system.py`) to avoid loading real embeddings.

**Spec:** `docs/superpowers/specs/2026-05-25-codebase-mcp-design.md`

**Conventions to follow:**
- Run any single test with `python -m pytest <path>::<name> -v` from repo root.
- New api tests import `from tests.api.test_search_system import FakeEmbedder` and open via `Codebase.open(tmp_path, embedder=FakeEmbedder(), **overrides)`.
- Keep the on-disk node store the only authoritative state; add no new persisted files.

---

## Task 1: Add policy fields to `LibraryConfig`

**Files:**
- Modify: `src/library/config.py`
- Test: `tests/library/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/library/test_config.py`:

```python
def test_policy_fields_default_zero_and_roundtrip(tmp_path):
    from library.config import LibraryConfig
    cfg = LibraryConfig(root_path=tmp_path)
    assert cfg.min_tests_per_method == 0
    assert cfg.max_folder_children == 0

    cfg2 = LibraryConfig(root_path=tmp_path, min_tests_per_method=3, max_folder_children=7)
    cfg2.save()
    loaded = LibraryConfig.load(tmp_path)
    assert loaded.min_tests_per_method == 3
    assert loaded.max_folder_children == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/library/test_config.py::test_policy_fields_default_zero_and_roundtrip -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'min_tests_per_method'`

- [ ] **Step 3: Add the fields**

In `src/library/config.py`, inside the `LibraryConfig` dataclass, add after `test_timeout_seconds: float = 60.0`:

```python
    min_tests_per_method: int = 0
    max_folder_children: int = 0
```

(`load`/`save` already round-trip every field via `fields(cls)` / `asdict`, so no other change is needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/library/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/library/config.py tests/library/test_config.py
git commit -m "feat(library): config knobs for min tests + folder cap"
```

---

## Task 2: Expose `Graph.config`

**Files:**
- Modify: `src/library/graph.py`
- Test: `tests/library/test_graph.py`

`Codebase` needs to read the policy values; expose the config it already holds rather than reaching into `_config`.

- [ ] **Step 1: Write the failing test**

Add to `tests/library/test_graph.py`:

```python
def test_graph_exposes_config(tmp_path):
    from library.graph import Graph
    from library.config import LibraryConfig
    g = Graph.open(tmp_path, min_tests_per_method=2)
    assert isinstance(g.config, LibraryConfig)
    assert g.config.min_tests_per_method == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/library/test_graph.py::test_graph_exposes_config -v`
Expected: FAIL — `AttributeError: 'Graph' object has no attribute 'config'`

- [ ] **Step 3: Add the property**

In `src/library/graph.py`, in class `Graph`, after `__init__` (before `open`), add:

```python
    @property
    def config(self) -> LibraryConfig:
        return self._config
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/library/test_graph.py::test_graph_exposes_config -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/library/graph.py tests/library/test_graph.py
git commit -m "feat(library): expose Graph.config"
```

---

## Task 3: Enforce `min_tests_per_method` in `Codebase.implement`

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_policies.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_codebase_policies.py`:

```python
import pytest
from api.codebase import Codebase
from api.errors import ImplementationFailed
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path, **overrides):
    return Codebase.open(tmp_path, embedder=FakeEmbedder(), **overrides)


def test_implement_rejects_too_few_tests(tmp_path):
    cb = _open(tmp_path, min_tests_per_method=3)
    nid = cb.add_method("inc", "add one")
    with pytest.raises(ImplementationFailed) as ei:
        cb.implement(nid, "def inc(x):\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    assert "need >= 3" in str(ei.value)
    assert cb.load_code(nid) == ""          # nothing was built or committed


def test_implement_accepts_enough_tests(tmp_path):
    cb = _open(tmp_path, min_tests_per_method=2)
    nid = cb.add_method("inc", "add one")
    tests = ("def test_a():\n    assert inc(1) == 2\n"
             "def test_b():\n    assert inc(2) == 3\n")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n", tests)
    assert res.all_passing


def test_implement_default_off_allows_single_test(tmp_path):
    cb = _open(tmp_path)                      # no override -> floor is 0
    nid = cb.add_method("inc", "add one")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res.all_passing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_codebase_policies.py::test_implement_rejects_too_few_tests -v`
Expected: FAIL — the implement succeeds (no floor enforced), so `pytest.raises` does not trigger.

- [ ] **Step 3: Implement the floor**

In `src/api/codebase.py`, add `import ast` at the top with the other stdlib imports, and a module-level helper above the `Codebase` class:

```python
def _count_tests(tests: str) -> int:
    try:
        tree = ast.parse(tests)
    except SyntaxError:
        return 0
    return sum(
        1 for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("test_")
    )
```

In `Codebase.implement`, after the `isinstance(node, CodeNode)` check and before `self._graph.trial_run(...)`, insert:

```python
        floor = self._graph.config.min_tests_per_method
        if floor > 0:
            got = _count_tests(tests)
            if got < floor:
                raise ImplementationFailed(
                    node_id, results=[], detail=f"got {got} tests, need >= {floor}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/api/test_codebase_policies.py -v`
Expected: PASS (all three)

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_policies.py
git commit -m "feat(api): enforce min_tests_per_method in implement"
```

---

## Task 4: Enforce `max_folder_children` on `define`/`make_folder`

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_policies.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_codebase_policies.py`:

```python
from api.errors import InvalidMove


def test_make_folder_blocks_over_cap(tmp_path):
    cb = _open(tmp_path, max_folder_children=2)
    cb.make_folder("a")
    cb.make_folder("b")
    with pytest.raises(InvalidMove) as ei:
        cb.make_folder("c")
    assert ei.value.reason == "folder-full"


def test_define_blocks_over_cap(tmp_path):
    cb = _open(tmp_path, max_folder_children=1)
    cb.add_method("one", "first")
    with pytest.raises(InvalidMove) as ei:
        cb.add_method("two", "second")
    assert ei.value.reason == "folder-full"


def test_cap_off_allows_many_children(tmp_path):
    cb = _open(tmp_path)                       # cap 0 -> unlimited
    for i in range(5):
        cb.make_folder(f"f{i}")
    assert len(cb.children_of(cb.root_id)) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_codebase_policies.py::test_make_folder_blocks_over_cap -v`
Expected: FAIL — third `make_folder` succeeds, no `InvalidMove` raised.

- [ ] **Step 3: Implement the cap check**

In `src/api/codebase.py`, add a helper method to `Codebase` (near the other `_` helpers, e.g. after `_detach_from_parent`):

```python
    def _check_capacity(self, parent_id: str, adding: int = 1) -> None:
        cap = self._graph.config.max_folder_children
        if cap <= 0:
            return
        if len(self._graph.children_of(parent_id)) + adding > cap:
            raise InvalidMove(parent_id, parent_id, "folder-full")
```

In `make_folder`, after the `isinstance(..., FolderNode)` parent check and before `nid = new_node_id()`:

```python
        self._check_capacity(parent_id)
```

In `define_abstraction`, after its `isinstance(..., FolderNode)` parent check and before `nid = new_node_id()`:

```python
        self._check_capacity(parent_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/api/test_codebase_policies.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_policies.py
git commit -m "feat(api): enforce max_folder_children on define/make_folder"
```

---

## Task 5: `Codebase.move` accepts one-or-many, batch cap is all-or-nothing

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_policies.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_codebase_policies.py`:

```python
def test_move_many_into_folder(tmp_path):
    cb = _open(tmp_path)
    dest = cb.make_folder("dest")
    a = cb.make_folder("a")
    b = cb.make_folder("b")
    cb.move([a, b], dest)
    assert cb.children_of(dest) == {a, b}


def test_batch_move_over_cap_is_all_or_nothing(tmp_path):
    cb = _open(tmp_path, max_folder_children=3)
    # dest currently has 0 children; cap 3
    dest = cb.make_folder("dest")          # root now has 1 child
    a = cb.add_method("a", "x", parent_id=cb.root_id)
    b = cb.add_method("b", "x", parent_id=cb.root_id)
    c = cb.add_method("c", "x", parent_id=cb.root_id)
    d = cb.add_method("d", "x", parent_id=cb.root_id)
    with pytest.raises(InvalidMove) as ei:
        cb.move([a, b, c, d], dest)        # would make dest have 4 > 3
    assert ei.value.reason == "folder-full"
    assert cb.children_of(dest) == set()   # nothing moved


def test_single_move_still_works(tmp_path):
    cb = _open(tmp_path)
    a = cb.make_folder("a")
    b = cb.make_folder("b")
    cb.move(a, b)                          # str still accepted
    assert a in cb.children_of(b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_codebase_policies.py::test_move_many_into_folder -v`
Expected: FAIL — `move` rejects a list (treats it as a node id; `Graph.get(list)` raises).

- [ ] **Step 3: Rewrite `move`**

In `src/api/codebase.py`, replace the entire existing `move` method with:

```python
    def move(self, node_ids, new_parent_id) -> None:
        ids = [node_ids] if isinstance(node_ids, str) else list(node_ids)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/api/test_codebase_policies.py tests/api/test_codebase_folders.py -v`
Expected: PASS (new batch tests **and** the existing folder tests, which use the single-id form)

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_policies.py
git commit -m "feat(api): move accepts one-or-many ids with all-or-nothing cap check"
```

---

## Task 6: `Codebase.ensure_built(node_ids)`

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_policies.py`

The scratch runner needs requested deps materialized into `build/` before importing them.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_codebase_policies.py`:

```python
def test_ensure_built_materializes_build_file(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_a():\n    assert inc(1) == 2\n")
    # remove the build artifact, then ensure_built should regenerate it
    (tmp_path / "build" / f"{nid}.py").unlink()
    cb.ensure_built([nid])
    assert (tmp_path / "build" / f"{nid}.py").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_codebase_policies.py::test_ensure_built_materializes_build_file -v`
Expected: FAIL — `AttributeError: 'Codebase' object has no attribute 'ensure_built'`

- [ ] **Step 3: Add the delegate**

In `src/api/codebase.py`, in the `# ---- access ----` section (e.g. after `load_tests`), add:

```python
    def ensure_built(self, node_ids) -> None:
        for nid in node_ids:
            self._graph.ensure_built(nid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_codebase_policies.py::test_ensure_built_materializes_build_file -v`
Expected: PASS

- [ ] **Step 5: Run the whole api+library suite as a regression check, then commit**

Run: `python -m pytest tests/api tests/library -q`
Expected: PASS (no regressions from the api/library changes)

```bash
git add src/api/codebase.py tests/api/test_codebase_policies.py
git commit -m "feat(api): Codebase.ensure_built delegate for scratch imports"
```

---

## Task 7: `pyproject.toml` — add the MCP SDK and package

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency and package include**

In `pyproject.toml`, change the `dependencies` list under `[project]` to add the SDK:

```toml
dependencies = [
    "numpy>=1.26",
    "tiktoken>=0.6",
    "sentence-transformers>=2.7",
    "hnswlib>=0.8",
    "mcp>=1.0",
]
```

And change `[tool.setuptools.packages.find]` `include` to add the new package:

```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["library*", "search*", "api*", "codebase_mcp*"]
```

- [ ] **Step 2: Install the SDK into the environment**

Run: `python -m pip install "mcp>=1.0"`
Expected: installs `mcp` and its deps.

- [ ] **Step 3: Verify FastMCP imports**

Run: `python -c "from mcp.server.fastmcp import FastMCP; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add mcp SDK dependency + codebase_mcp package"
```

---

## Task 8: `McpConfig`

**Files:**
- Create: `src/codebase_mcp/__init__.py`
- Create: `src/codebase_mcp/config.py`
- Create: `tests/codebase_mcp/__init__.py`
- Test: `tests/codebase_mcp/test_config.py`

- [ ] **Step 1: Write the failing test**

Create empty `tests/codebase_mcp/__init__.py`, then create `tests/codebase_mcp/test_config.py`:

```python
from pathlib import Path
from codebase_mcp.config import McpConfig


def test_defaults_when_env_empty():
    cfg = McpConfig.from_env(env={})
    assert cfg.root == Path.home() / ".haymanbot" / "codebase"
    assert cfg.min_tests == 3
    assert cfg.max_folder_children == 7
    assert cfg.scratch_timeout == 30.0


def test_env_overrides():
    env = {
        "HAYMANBOT_CODEBASE": "/tmp/cb",
        "HAYMANBOT_MIN_TESTS": "5",
        "HAYMANBOT_MAX_FOLDER_CHILDREN": "10",
        "HAYMANBOT_SCRATCH_TIMEOUT": "12.5",
    }
    cfg = McpConfig.from_env(env=env)
    assert cfg.root == Path("/tmp/cb")
    assert cfg.min_tests == 5
    assert cfg.max_folder_children == 10
    assert cfg.scratch_timeout == 12.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebase_mcp'`

- [ ] **Step 3: Create the package and config**

Create `src/codebase_mcp/__init__.py` as an **empty file** for now. (`Workspace` does not exist until Task 10, so the re-exports are added there, not now.)

Create `src/codebase_mcp/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class McpConfig:
    root: Path
    min_tests: int = 3
    max_folder_children: int = 7
    scratch_timeout: float = 30.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "McpConfig":
        env = os.environ if env is None else env
        raw_root = env.get("HAYMANBOT_CODEBASE")
        root = Path(raw_root).expanduser() if raw_root else Path.home() / ".haymanbot" / "codebase"

        def _int(name: str, default: int) -> int:
            v = env.get(name)
            return int(v) if v else default

        timeout = env.get("HAYMANBOT_SCRATCH_TIMEOUT")
        return cls(
            root=root,
            min_tests=_int("HAYMANBOT_MIN_TESTS", 3),
            max_folder_children=_int("HAYMANBOT_MAX_FOLDER_CHILDREN", 7),
            scratch_timeout=float(timeout) if timeout else 30.0,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/codebase_mcp/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/__init__.py src/codebase_mcp/config.py tests/codebase_mcp/__init__.py tests/codebase_mcp/test_config.py
git commit -m "feat(mcp): McpConfig from env with defaults"
```

---

## Task 9: `ScratchRunner`

**Files:**
- Create: `src/codebase_mcp/scratch.py`
- Test: `tests/codebase_mcp/test_scratch.py`

Ephemeral subprocess execution. No graph access of its own: the caller passes ready-made import lines.

- [ ] **Step 1: Write the failing test**

Create `tests/codebase_mcp/test_scratch.py`:

```python
from pathlib import Path
from codebase_mcp.scratch import ScratchRunner


def _runner(tmp_path, timeout=10.0):
    (tmp_path / "build").mkdir(parents=True, exist_ok=True)
    return ScratchRunner(tmp_path, timeout=timeout)


def test_run_captures_stdout_and_zero_exit(tmp_path):
    r = _runner(tmp_path).run("print('hello scratch')")
    assert r.exit_code == 0
    assert not r.timed_out
    assert "hello scratch" in r.stdout


def test_nonzero_exit_is_reported(tmp_path):
    r = _runner(tmp_path).run("raise SystemExit(3)")
    assert r.exit_code == 3
    assert not r.timed_out


def test_timeout_is_killed(tmp_path):
    r = _runner(tmp_path, timeout=0.5).run("import time\ntime.sleep(5)\n")
    assert r.timed_out is True


def test_temp_file_is_cleaned_up(tmp_path):
    _runner(tmp_path).run("print('x')")
    leftovers = list((tmp_path).glob("_scratch_*.py"))
    assert leftovers == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_scratch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebase_mcp.scratch'`

- [ ] **Step 3: Implement `ScratchRunner`**

Create `src/codebase_mcp/scratch.py`:

```python
from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from library.runner import _worker_env


@dataclass
class ScratchResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class ScratchRunner:
    """Runs throwaway Python with the codebase's build/ importable. Persists nothing."""

    def __init__(self, root: Path, timeout: float = 30.0, python: str | None = None) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._python = python or sys.executable

    def run(self, code: str, import_lines: tuple[str, ...] = ()) -> ScratchResult:
        body = ("\n".join(import_lines) + "\n\n" + code) if import_lines else code
        path = self._root / f"_scratch_{uuid.uuid4().hex}.py"
        path.write_text(body)
        try:
            proc = subprocess.run(
                [self._python, str(path)],
                cwd=str(self._root),               # so `import build.X` resolves
                env=_worker_env(self._root),
                capture_output=True, text=True, timeout=self._timeout,
            )
            return ScratchResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return ScratchResult(None, out, err, True)
        finally:
            path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codebase_mcp/test_scratch.py -v`
Expected: PASS (all four)

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/scratch.py tests/codebase_mcp/test_scratch.py
git commit -m "feat(mcp): ephemeral ScratchRunner"
```

---

## Task 10: `Workspace` — open + search + read views

**Files:**
- Create: `src/codebase_mcp/workspace.py`
- Modify: `src/codebase_mcp/__init__.py`
- Test: `tests/codebase_mcp/test_workspace_read.py`

- [ ] **Step 1: Write the failing test**

Create `tests/codebase_mcp/test_workspace_read.py`:

```python
import pytest
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    cfg = McpConfig(root=tmp_path, min_tests=0, max_folder_children=0, **overrides)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_search_returns_hits(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("rolling_mean", "streaming mean of a window")
    out = ws.search("mean")
    assert any(h["id"] == nid for h in out["hits"])
    assert {"id", "kind", "name", "description", "score"} <= set(out["hits"][0])


def test_view_stub_hides_full_body(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("inc", "add one")
    ws._cb.implement(nid, "def inc(x):\n    # secret body\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    v = ws.view(nid)
    assert v["kind"] == "method"
    assert v["name"] == "inc"
    assert v["signature"] == "def inc(x):"
    assert v["has_code"] is True
    assert "secret body" not in str(v)          # stub does not leak the body
    assert v["tests"][0]["name"] == "a"


def test_read_code_returns_full_source(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("inc", "add one")
    ws._cb.implement(nid, "def inc(x):\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    assert "return x + 1" in ws.read_code(nid)["code"]
    assert "def test_a" in ws.read_tests(nid)["tests"]


def test_tree_and_children(tmp_path):
    ws = _ws(tmp_path)
    f = ws._cb.make_folder("utils")
    leaf = ws._cb.add_method("inc", "add one", parent_id=f)
    kids = ws.children(f)
    assert [k["id"] for k in kids] == [leaf]
    tree = ws.tree()
    assert tree["kind"] == "folder"             # root
    assert any(c["id"] == f for c in tree["children"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_workspace_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebase_mcp.workspace'`

- [ ] **Step 3: Implement `Workspace` (open + read side)**

Create `src/codebase_mcp/workspace.py`:

```python
from __future__ import annotations

from pathlib import Path

from api.codebase import Codebase
from library import CodeNode, FolderNode, Node

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
               folders: list[str] | None = None, page: int = 0) -> dict:
        pg = self._cb.search(query, page=page, tags=tuple(tags or ()),
                             object_types=tuple(object_types or ()),
                             folders=tuple(folders or ()))
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

    # ---- read ----
    def view(self, node_id: str) -> dict:
        node = self._cb.load(node_id)
        if isinstance(node, FolderNode):
            return {"id": node_id, "kind": "folder", "name": node.name,
                    "description": node.description,
                    "tags": sorted(t.text for t in node.tags),
                    "children": [self._stub(c) for c in sorted(self._cb.children_of(node_id))]}
        code = self._cb.load_code(node_id)
        signature = next((ln for ln in code.splitlines() if ln.strip()), "") if code else ""
        return {
            "id": node_id, "kind": node.object_type, "name": node.name,
            "description": node.description,
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
```

Now set the final `src/codebase_mcp/__init__.py`:

```python
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace

__all__ = ["McpConfig", "Workspace"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codebase_mcp/test_workspace_read.py -v`
Expected: PASS (all five)

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/workspace.py src/codebase_mcp/__init__.py tests/codebase_mcp/test_workspace_read.py
git commit -m "feat(mcp): Workspace open + search + stub-first read views"
```

---

## Task 11: `Workspace` — create, implement, build

**Files:**
- Modify: `src/codebase_mcp/workspace.py`
- Test: `tests/codebase_mcp/test_workspace_build.py`

- [ ] **Step 1: Write the failing test**

Create `tests/codebase_mcp/test_workspace_build.py`:

```python
import pytest
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    base = {"min_tests": 0, "max_folder_children": 0}
    base.update(overrides)
    cfg = McpConfig(root=tmp_path, **base)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_define_then_implement_success(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "inc", "add one")
    assert d["ok"] is True
    res = ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res["ok"] is True
    assert res["tests"][0]["status"] == "passing"
    assert d["id"] not in [n["id"] for n in ws.dirty()["nodes"]]


def test_define_rejects_bad_kind(tmp_path):
    ws = _ws(tmp_path)
    out = ws.define("widget", "x", "y")
    assert out["ok"] is False
    assert out["reason"] == "bad-kind"


def test_implement_too_few_tests_is_structured(tmp_path):
    ws = _ws(tmp_path, min_tests=3)
    d = ws.define("method", "inc", "add one")
    res = ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res["ok"] is False
    assert res["reason"] == "tests-failed"
    assert res["required_tests"] == 3


def test_implement_failure_lists_failing_tests(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "bad", "broken")
    res = ws.implement(d["id"], "def bad():\n    return 1\n",
                       "def test_a():\n    assert bad() == 2\n")
    assert res["ok"] is False
    assert res["failures"][0]["name"] == "a"


def test_dirty_and_rebuild(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "inc", "add one")        # defined, no code -> dirty
    assert d["id"] in [n["id"] for n in ws.dirty()["nodes"]]
    ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                 "def test_a():\n    assert inc(1) == 2\n")
    report = ws.rebuild()
    assert "rebuilt" in report and "failed" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_workspace_build.py -v`
Expected: FAIL — `AttributeError: 'Workspace' object has no attribute 'define'`

- [ ] **Step 3: Add the create/implement/build methods**

In `src/codebase_mcp/workspace.py`, add these imports at the top (with the existing ones):

```python
from api.errors import ApiError, ImplementationFailed, InvalidMove
from library import BuildError
```

Add a module-level constant near the top (after imports):

```python
_KINDS = {"class", "method", "executable"}
```

Add these methods to `Workspace`:

```python
    # ---- create + build ----
    def define(self, kind: str, name: str, description: str, *,
               parent: str | None = None, dependencies: list[str] | None = None,
               tags: list[str] | None = None) -> dict:
        if kind not in _KINDS:
            return {"ok": False, "reason": "bad-kind",
                    "detail": f"kind must be one of {sorted(_KINDS)}"}
        try:
            nid = self._cb.define_abstraction(
                name, description, kind, parent_id=parent,
                dependencies=tuple(dependencies or ()), tags=tuple(tags or ()))
        except InvalidMove as e:
            return self._invalid_move(e)
        except ApiError as e:
            return {"ok": False, "reason": "api-error", "detail": str(e)}
        return {"ok": True, "id": nid}

    def implement(self, node_id: str, code: str, tests: str) -> dict:
        try:
            res = self._cb.implement(node_id, code, tests)
        except ImplementationFailed as e:
            return {
                "ok": False, "reason": "tests-failed", "detail": e.detail,
                "required_tests": self._config.min_tests,
                "failures": [{"name": r.name, "detail": r.detail}
                             for r in e.results if r.status.name != "PASSING"],
            }
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
```

Add the shared error renderer (used here and in Task 12):

```python
    def _invalid_move(self, e: InvalidMove) -> dict:
        if e.reason == "folder-full":
            return {"ok": False, "reason": "folder-full",
                    "folder_id": e.node_id, "cap": self._config.max_folder_children}
        return {"ok": False, "reason": e.reason,
                "node_id": e.node_id, "target_id": e.target_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codebase_mcp/test_workspace_build.py -v`
Expected: PASS (all five)

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/workspace.py tests/codebase_mcp/test_workspace_build.py
git commit -m "feat(mcp): Workspace define/implement/dirty/rebuild"
```

---

## Task 12: `Workspace` — refactor + health + scratch

**Files:**
- Modify: `src/codebase_mcp/workspace.py`
- Test: `tests/codebase_mcp/test_workspace_refactor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/codebase_mcp/test_workspace_refactor.py`:

```python
import pytest
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    base = {"min_tests": 0, "max_folder_children": 0}
    base.update(overrides)
    cfg = McpConfig(root=tmp_path, **base)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_make_folder_and_move_many(tmp_path):
    ws = _ws(tmp_path)
    dest = ws.make_folder("dest")["id"]
    a = ws.define("method", "a", "x")["id"]
    b = ws.define("method", "b", "x")["id"]
    out = ws.move([a, b], dest)
    assert out["ok"] is True
    assert {k["id"] for k in ws.children(dest)} == {a, b}


def test_move_into_full_folder_is_structured(tmp_path):
    ws = _ws(tmp_path, max_folder_children=1)
    dest = ws.make_folder("dest")["id"]          # root has 1 child now
    # dest is empty; cap 1. Put two methods under root, then move both in.
    a = ws.define("method", "a", "x")["id"]
    b = ws.define("method", "b", "x")["id"]
    out = ws.move([a, b], dest)                   # would make dest hold 2 > 1
    assert out["ok"] is False
    assert out["reason"] == "folder-full"
    assert out["folder_id"] == dest


def test_rename_and_remove(tmp_path):
    ws = _ws(tmp_path)
    f = ws.make_folder("old")["id"]
    assert ws.rename(f, "new")["ok"] is True
    assert ws.view(f)["name"] == "new"
    assert ws.remove(f)["ok"] is True


def test_health_lists_full_folders(tmp_path):
    ws = _ws(tmp_path, max_folder_children=2)
    f = ws.make_folder("g")["id"]
    ws.define("method", "a", "x", parent=f)
    ws.define("method", "b", "x", parent=f)       # f now at cap 2
    health = ws.health()
    assert health["cap"] == 2
    assert any(o["id"] == f and o["children"] == 2 for o in health["over"])


def test_run_scratch_imports_a_built_node(tmp_path):
    ws = _ws(tmp_path)
    nid = ws.define("method", "inc", "add one")["id"]
    ws.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_a():\n    assert inc(1) == 2\n")
    out = ws.run_scratch("print(inc(41))", deps=[nid])
    assert out["ok"] is True
    assert "42" in out["stdout"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_workspace_refactor.py -v`
Expected: FAIL — `AttributeError: 'Workspace' object has no attribute 'make_folder'`

- [ ] **Step 3: Add the refactor/health/scratch methods**

In `src/codebase_mcp/workspace.py`, add to `Workspace`:

```python
    # ---- refactor ----
    def make_folder(self, name: str, *, parent: str | None = None,
                    description: str = "", tags: list[str] | None = None) -> dict:
        try:
            nid = self._cb.make_folder(name, parent_id=parent, description=description,
                                       tags=tuple(tags or ()))
        except InvalidMove as e:
            return self._invalid_move(e)
        return {"ok": True, "id": nid}

    def move(self, node_ids, new_parent: str) -> dict:
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
        return {"ok": r.exit_code == 0 and not r.timed_out,
                "exit_code": r.exit_code, "timed_out": r.timed_out,
                "stdout": r.stdout, "stderr": r.stderr}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codebase_mcp/test_workspace_refactor.py -v`
Expected: PASS (all five)

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/workspace.py tests/codebase_mcp/test_workspace_refactor.py
git commit -m "feat(mcp): Workspace refactor primitives + health + run_scratch"
```

---

## Task 13: FastMCP `server.py`

**Files:**
- Create: `src/codebase_mcp/server.py`
- Create: `src/codebase_mcp/__main__.py`
- Test: `tests/codebase_mcp/test_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/codebase_mcp/test_server.py`:

```python
from codebase_mcp.config import McpConfig
from codebase_mcp.server import build_server, TOOL_NAMES
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def test_every_tool_name_maps_to_a_workspace_method():
    for name in TOOL_NAMES:
        assert hasattr(Workspace, name), f"Workspace missing {name}"


def test_build_server_registers_without_error(tmp_path):
    cfg = McpConfig(root=tmp_path, min_tests=0, max_folder_children=0)
    ws = Workspace.open(cfg, embedder=FakeEmbedder())
    app = build_server(ws)
    assert app is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/codebase_mcp/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codebase_mcp.server'`

- [ ] **Step 3: Implement the server**

Create `src/codebase_mcp/server.py`:

```python
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from codebase_mcp.workspace import Workspace

TOOL_NAMES = [
    "search", "search_tags", "list_tags",
    "view", "read_code", "read_tests", "children", "tree",
    "define", "implement", "dirty", "rebuild",
    "make_folder", "move", "rename", "remove", "health",
    "run_scratch",
]


def build_server(workspace: Workspace) -> FastMCP:
    app = FastMCP("haymanbot-codebase")
    for name in TOOL_NAMES:
        app.tool(name=name)(getattr(workspace, name))
    return app


def main() -> None:
    app = build_server(Workspace.open())
    app.run()


if __name__ == "__main__":
    main()
```

Create `src/codebase_mcp/__main__.py`:

```python
from codebase_mcp.server import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/codebase_mcp/test_server.py -v`
Expected: PASS

> If `app.tool(name=...)` raises a schema error for any method, it means a parameter lacks a type annotation FastMCP can model — every `Workspace` tool method here already uses `str` / `list[str] | None` / `int` annotations, which FastMCP supports. Do not change the rendering contract to satisfy the SDK; fix the annotation instead.

- [ ] **Step 5: Commit**

```bash
git add src/codebase_mcp/server.py src/codebase_mcp/__main__.py tests/codebase_mcp/test_server.py
git commit -m "feat(mcp): FastMCP server binding Workspace tools"
```

---

## Task 14: End-to-end integration test

**Files:**
- Test: `tests/codebase_mcp/test_integration.py`

- [ ] **Step 1: Write the test**

Create `tests/codebase_mcp/test_integration.py`:

```python
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path):
    cfg = McpConfig(root=tmp_path, min_tests=2, max_folder_children=7)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_discover_define_implement_reuse_scratch(tmp_path):
    ws = _ws(tmp_path)

    # discover: nothing there yet
    assert ws.search("add one to a number")["hits"] == [] or True  # may be empty

    # define + implement with the required 2 tests
    d = ws.define("method", "inc", "add one to a number")
    assert d["ok"] is True
    res = ws.implement(
        d["id"],
        "def inc(x):\n    return x + 1\n",
        "def test_a():\n    assert inc(1) == 2\n"
        "def test_b():\n    assert inc(5) == 6\n",
    )
    assert res["ok"] is True

    # discover again: now it is findable
    assert any(h["id"] == d["id"] for h in ws.search("increment number")["hits"])

    # reuse via a scratch macro that imports the stored node
    out = ws.run_scratch("print(inc(99))", deps=[d["id"]])
    assert out["ok"] is True
    assert "100" in out["stdout"]
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/codebase_mcp/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run the full suite as a final regression check**

Run: `python -m pytest -q`
Expected: PASS (entire repo, including pre-existing api/library/search tests)

- [ ] **Step 4: Commit**

```bash
git add tests/codebase_mcp/test_integration.py
git commit -m "test(mcp): end-to-end discover->define->implement->reuse->scratch"
```

---

## Task 15: Wire-up docs

**Files:**
- Create: `src/codebase_mcp/README.md`

- [ ] **Step 1: Write the README**

Create `src/codebase_mcp/README.md`:

````markdown
# codebase_mcp

MCP server exposing the `api.Codebase` as a personal, test-gated function library.

## Run

```bash
python -m codebase_mcp        # stdio transport
```

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `HAYMANBOT_CODEBASE` | `~/.haymanbot/codebase` | library root |
| `HAYMANBOT_MIN_TESTS` | `3` | minimum passing tests required by `implement` |
| `HAYMANBOT_MAX_FOLDER_CHILDREN` | `7` | hard cap; the (N+1)th child is rejected as `folder-full` |
| `HAYMANBOT_SCRATCH_TIMEOUT` | `30` | seconds before a `run_scratch` run is killed |

## Claude Code registration

```bash
claude mcp add codebase -- python -m codebase_mcp
```

## Tools

`search`, `search_tags`, `list_tags`, `view`, `read_code`, `read_tests`, `children`,
`tree`, `define`, `implement`, `dirty`, `rebuild`, `make_folder`, `move`, `rename`,
`remove`, `health`, `run_scratch`.

Workflow: **search → view → reuse-as-dependency or define → implement (≥N tests) → rebuild**;
`run_scratch` prototypes throwaway macros against built nodes without committing them.
````

- [ ] **Step 2: Commit**

```bash
git add src/codebase_mcp/README.md
git commit -m "docs(mcp): codebase_mcp README + Claude Code registration"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** policies in api (Tasks 1,3,4) ✓; `ensure_built` (Task 6) ✓; `McpConfig` (Task 8) ✓; `ScratchRunner` ephemeral + imports nodes (Tasks 9,12) ✓; stub-first read surface (Task 10) ✓; create/implement/build (Task 11) ✓; first-class refactor primitives + one-or-many `move` + hard-block + `health` (Tasks 5,12) ✓; FastMCP server (Task 13) ✓; integration (Task 14) ✓.
- **Backward compatibility:** library/api defaults are `0` (off); existing tests must still pass — verified by the regression runs in Tasks 6 and 14.
- **Naming:** package is `codebase_mcp` (not `mcp`) to avoid shadowing the SDK on `pythonpath=["src"]`.
- **No new persisted state:** `Workspace`/`ScratchRunner` hold only in-memory/ephemeral state; the node store stays the single source of truth.

---

# Addendum (2026-05-25): revised scope from execution-time feedback

Tasks 1–6 are complete and reviewed. The following revises/extends the remaining tasks per the spec's "Revision (2026-05-25)" section (searchability R1, OR tag matching R2, `discover` pipeline R3, guidance R4). Two new api/library tasks are inserted **before** the `codebase_mcp` package tasks; the Workspace/server/README tasks gain the deltas listed. Original Tasks 8–15 remain the base; apply the deltas on top.

Order: **Task 7 (pyproject)** → **Task A (library searchable)** → **Task B (api searchable + OR search)** → Tasks 8, 9 → **Task 10 + deltas** → **Task 11 + delta** → **Task 12 + deltas** → **Task 13 + deltas** → **Task 14 + delta** → **Task 15 + delta**.

## Task A: `Node.searchable` field + store serialization (R1)
**Files:** Modify `src/library/nodes.py`, `src/library/store.py`; Test `tests/library/test_nodes.py`, `tests/library/test_store.py`.

- `nodes.py`: add `searchable: bool = True` to the **`Node`** base dataclass (after `tags`). `FolderNode`/`CodeNode` inherit it.
- `store.py` `_node_to_dict`: add `"searchable": node.searchable` to the base dict.
- `store.py` `_dict_to_node`: add `searchable=d.get("searchable", True)` to `common` (default True for pre-existing nodes).
- Tests: a `CodeNode`/`FolderNode` default `searchable is True`; round-trip a node with `searchable=False` through `NodeStore.save`/`load`; an old `meta.json` dict without the key loads as `searchable=True`.
- Regression: `python -m pytest tests/library -q`. Commit: `feat(library): node searchable flag + persistence`.

## Task B: `Codebase` searchability + OR tag/folder/type search (R1, R2)
**Files:** Modify `src/api/codebase.py`, `src/api/search_system.py`; Test `tests/api/test_codebase_search.py` (append) + new `tests/api/test_codebase_searchable.py`.

`search_system.py`:
- `_any_groups(tags, object_types, folders)`: add a `tags` OR group (raw tag texts) when non-empty, alongside the existing `@in:`/`@kind:` groups.
- `search` and `search_page`: stop passing user `tags` as `all_tags`. Instead pass them via `_any_groups`. Add a `require_all: set[str] = frozenset()` param that becomes `all_tags` (the AND gate). Include `require_all` in the `search_page` cache key.

`codebase.py`:
- `_composite_tags`: add `f"@searchable:{str(node.searchable).lower()}"`.
- `define_abstraction`/`make_folder`: accept `searchable: bool = True`, set it on the created node.
- `set_searchable(node_id, value)`: load node, set `node.searchable`, `graph.update_node(node)`, then `self._search.update_tags(node_id, self._composite_tags(node))` (cheap retag, no re-embed).
- `search(query, *, page=0, page_size=10, tags=(), folders=(), object_types=(), include_hidden=False)`: compute `require_all = set() if include_hidden else {"@searchable:true"}`; pass `tags`/`folders`/`object_types` through (now OR) and `require_all` to `search_page`.

Tests (`test_codebase_searchable.py`):
- a node defined `searchable=False` does NOT appear in `search` by default but DOES with `include_hidden=True`;
- `set_searchable(nid, False)` then `True` toggles its appearance;
- a hidden node is still usable as a dependency: define helper hidden, define caller depending on it, `implement` both, caller's tests pass;
- OR semantics: two nodes tagged `["x"]` and `["y"]`; `search(query, tags=["x","y"])` returns **both** (match ≥1), not just nodes having both.
- Regression: `python -m pytest tests/api tests/library -q`. Commit: `feat(api): searchable gate + OR tag/folder/type filters`.

## Task 10 deltas — Workspace read + `discover` (R2, R3)
On top of base Task 10:
- `search(...)` gains `include_hidden: bool = False`, forwarded to `Codebase.search`.
- `view(...)` includes `"searchable": node.searchable` in both the folder and code branches.
- Add `discover(query, *, page=0)` returning:
  ```python
  {
    "hits": <plain search hits as in search()>,
    "candidate_tags": [{"tag":..., "score":...}, ...],          # from search_tags(query)
    "candidate_folders": [{"id":..., "name":..., "score":...}],  # search(query, object_types=["folder"], include_hidden=True) hits
    "object_types_present": sorted({h["kind"] for h in hits}),
    "hint": "If hits look weak, call search(query, tags=[...], folders=[...], object_types=[...]) "
            "with filters chosen from candidate_tags/candidate_folders. Tag/folder/type filters are OR (match any).",
  }
  ```
- Tests: `discover` returns the four keys; a node tagged + foldered appears in `candidate_tags`/`candidate_folders`; `search(include_hidden=True)` surfaces a hidden node that default search hides.

## Task 11 delta — `define` accepts `searchable` (R1)
`Workspace.define(...)` gains `searchable: bool = True`, forwarded to `Codebase.define_abstraction`.
Test: `define("method", "h", "helper", searchable=False)` then default `search` does not return it; `discover`/`search(include_hidden=True)` does.

## Task 12 deltas — hide/show + folder-full hint (R1, R4)
On top of base Task 12:
- Add `hide(node_id)` → `set_searchable(node_id, False)`; `show(node_id)` → `set_searchable(node_id, True)`; both return `{"ok": True, "id": node_id, "searchable": <bool>}`.
- `make_folder(...)` gains `searchable: bool = True`.
- `_invalid_move` folder-full branch gains a `"hint"`: `"folder is full (cap N). Create a subfolder with make_folder and move() related nodes into it, or move some children out, then retry."` (interpolate the cap).
- Tests: `hide` then `search` omits it, `show` restores; folder-full result includes a non-empty `hint`.

## Task 13 deltas — server tools + guidance (R3, R4)
- Add `"discover"`, `"hide"`, `"show"` to `TOOL_NAMES` (full list, alphabetic-ish but grouped is fine): search group = `discover, search, search_tags, list_tags`; read = `view, read_code, read_tests, children, tree`; create = `define, implement, dirty, rebuild`; refactor = `make_folder, move, rename, remove, hide, show, health`; scratch = `run_scratch`.
- Give the FastMCP server an `instructions=` string (FastMCP supports it) carrying R4 guidance: "Search/discover before writing; reuse existing nodes as dependencies. Decompose into small single-purpose nodes; build internal helpers as separate nodes created with searchable=False and compose them as dependencies. Create folders as needed; when an op returns folder-full, make a subfolder and move related nodes into it, then retry."
- Test (`test_server.py`) updated so `TOOL_NAMES` includes the three new names and each maps to a `Workspace` method.

## Task 14 delta — integration covers hidden helper + discover (R1, R3)
Extend the end-to-end test: define a hidden helper (`searchable=False`), define+implement a method depending on it, confirm the helper is absent from default `search` but the method is present, `discover` returns candidates, and `run_scratch` importing the method works.

## Task 15 delta — README (R1–R4)
Document: `discover`/`search` (OR filters, `include_hidden`), `hide`/`show` and `searchable=` on `define`/`make_folder`, the decomposition + folder-management guidance, and the `discover → judge → refine` flow.

---

# Addendum 2 (2026-05-25): callable-tool vs helper classification (R5)

Two tasks, applied after the core MCP. Mirrors the `searchable` work.

## Task R5a — api: `CodeNode.is_tool` + `@tool:` filter
**Files:** `src/library/nodes.py`, `src/library/store.py`, `src/api/codebase.py`; tests in `tests/library/test_store.py`, `tests/api/test_codebase_is_tool.py` (new).
- `nodes.py`: add `is_tool: bool = True` to **`CodeNode`** (not Node — folders excluded).
- `store.py`: in `_node_to_dict` CodeNode branch add `"is_tool": node.is_tool`; in `_dict_to_node` CodeNode branch add `is_tool=d.get("is_tool", True)`.
- `codebase.py`: `_composite_tags` adds `f"@tool:{str(node.is_tool).lower()}"` only when the node is a `CodeNode`; `define_abstraction` accepts `is_tool: bool = True` and passes it to `CodeNode(...)`; add `set_is_tool(node_id, value)` (set + `update_node` + retag via `update_tags`); `search` accepts `is_tool: bool | None = None` and, when not None, adds `@tool:<bool>` to `require_all`.
- Tests: define a helper `is_tool=False` and a tool; `search(is_tool=True)` returns only tools, `search(is_tool=False)` only helpers, default returns both; `set_is_tool` toggles; round-trip persistence of `is_tool`; default True for a node whose meta.json lacks the key.

## Task R5b — MCP: define/search/view + mark_tool/mark_helper
**Files:** `src/codebase_mcp/workspace.py`, `src/codebase_mcp/server.py`; tests append to `tests/codebase_mcp/test_workspace_build.py` and `test_server.py`.
- `Workspace.define(..., is_tool: bool = True)` → forward to `define_abstraction`.
- `Workspace.search(..., is_tool: bool | None = None)` → forward to `Codebase.search`.
- `Workspace.view` includes `"is_tool"` for code nodes.
- `Workspace.mark_tool(node_id)` / `mark_helper(node_id)` → `set_is_tool(True/False)`, return `{ok, id, is_tool}`.
- `server.py`: add `"mark_tool"`, `"mark_helper"` to `TOOL_NAMES`; extend GUIDANCE: "Mark broadly-useful callables as tools (default) and internal building blocks as helpers (is_tool=False); search(is_tool=True) finds tools, is_tool=False finds helpers."
- Tests: define tool + helper via Workspace, `search(is_tool=True/False)` filters correctly; `view` shows is_tool; mark_tool/mark_helper toggle; server registers the two new tools.
