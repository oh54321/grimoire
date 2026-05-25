# Codebase API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `src/api/` package exposing a single `Codebase` facade that lets Claude grow a hierarchical codebase — define abstractions, implement+test them transactionally, organize folders, incrementally rebuild, and navigate by fast filtered vector search with paged, rendered results.

**Architecture:** Three objects. `Graph` (existing `src/library/`) owns nodes/build/test. `SearchSystem` (new) owns two vector indices and answers filtered/paged search. `Codebase` (new) owns and coordinates both, deriving `root_id` and `dirty()` so the on-disk node store stays the single source of truth. Filtering is encoded as composite tags (`@kind:`, `@in:`); `implement` uses a staged atomic commit; tests run through a warm pytest worker.

**Tech Stack:** Python 3.11+, numpy, hnswlib, sentence-transformers, pytest + pytest-json-report. Spec: `docs/superpowers/specs/2026-05-24-codebase-api-design.md`.

---

## File Structure

**New (`src/api/`):**
- `errors.py` — `ApiError`, `ImplementationFailed`, `InvalidMove`
- `results.py` — `SearchHit`, `TagHit`, `SearchPage`, `TagPage`, `ImplementResult`, `RebuildReport`
- `search_system.py` — `SearchSystem` (two indices, composite-tag search, paging+LRU cache, reindex)
- `codebase.py` — `Codebase` facade
- `__init__.py` — re-exports

**Modified (`src/search/`):** `tagged_kvdb.py` (keyed identity, `delete`, `update_tags`, `search_filtered`; bump to v3).

**Modified (`src/library/`):** `config.py` (worker flags), `runner.py` (warm worker + parse extraction), `builder.py` (`build_trial`, `is_stale_with_deps`), `graph.py` (`iter_ids`, `iter_code_ids`, `is_build_stale`, `trial_run`, `discard_trial`); new `library/_test_worker.py`.

**Modified (root):** `pyproject.toml` (include `api*`).

**Tests:** mirror under `tests/api/`, `tests/search/`, `tests/library/`.

---

## Phase A — `search` package extensions (v3)

### Task A1: Keyed identity + delete on `TaggedKVDatabase`

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Test: `tests/search/test_tagged_kvdb_keyed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/search/test_tagged_kvdb_keyed.py
import numpy as np
import pytest
from search.tagged_kvdb import TaggedKVDatabase


class FakeEmbedder:
    model_name = "fake"
    dim = 8

    def encode(self, phrase: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def test_identical_phrases_distinct_keys_both_persist():
    db = TaggedKVDatabase(embedder=FakeEmbedder())
    db.add("parse the input", {"id": "n1"}, tags=["a"], key="n1")
    db.add("parse the input", {"id": "n2"}, tags=["b"], key="n2")  # same phrase, different key
    assert len(db) == 2
    # both retrievable by their tags
    assert {tuple(v.items()) for v in db.list_by_tags(["a"])} == {(("id", "n1"),)}
    assert {tuple(v.items()) for v in db.list_by_tags(["b"])} == {(("id", "n2"),)}


def test_delete_by_key_removes_entry():
    db = TaggedKVDatabase(embedder=FakeEmbedder())
    db.add("x", {"id": "n1"}, tags=["a"], key="n1")
    db.delete("n1")
    assert len(db) == 0
    assert db.list_by_tags(["a"]) == []
    db.delete("n1")  # idempotent, no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/search/test_tagged_kvdb_keyed.py -v`
Expected: FAIL — `add()` has no `key` kwarg / no `delete` method.

- [ ] **Step 3: Modify `tagged_kvdb.py`**

Bump the version and rewrite `add` to dedup on an explicit identity key (default = phrase), and add `delete`. The store's `phrase_to_id` map now holds **keys**, while `id_to_phrase` keeps the embedded phrase.

```python
TAGGED_STORE_VERSION = 3   # was 2: key/identity split changes persisted semantics
```

Replace the `add` method body:

```python
    def add(
        self,
        phrase: str,
        value: JSONValue,
        tags: Iterable[str] = (),
        *,
        key: str | None = None,
    ) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"value not JSON-serialisable: {e}") from e

        identity = phrase if key is None else key
        tag_set = _validate_tags(tags)
        vec = self._encode(phrase)

        with self._lock.write():
            old_id = self._store.phrase_to_id.get(identity)
            if old_id is not None:
                self._index.mark_deleted(old_id)
                del self._store.id_to_value[old_id]
                del self._store.id_to_phrase[old_id]
                self._remove_id_from_tags(old_id)

            new_id = self._store.next_id
            self._store.next_id += 1
            self._grow_for(new_id)

            self._index.add_items(vec.reshape(1, -1), np.array([new_id]))
            self._store.phrase_to_id[identity] = new_id
            self._store.id_to_value[new_id] = value
            self._store.id_to_phrase[new_id] = phrase
            self._add_id_to_tags(new_id, tag_set)

    def delete(self, key: str) -> None:
        """Remove the entry identified by `key`. No-op if absent."""
        with self._lock.write():
            id_ = self._store.phrase_to_id.pop(key, None)
            if id_ is None:
                return
            self._index.mark_deleted(id_)
            self._store.id_to_value.pop(id_, None)
            self._store.id_to_phrase.pop(id_, None)
            self._remove_id_from_tags(id_)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/search/test_tagged_kvdb_keyed.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing search suite for regressions**

Run: `pytest tests/search -q`
Expected: PASS (KVDatabase unaffected: its `key` defaults to phrase).

- [ ] **Step 6: Commit**

```bash
git add src/search/tagged_kvdb.py tests/search/test_tagged_kvdb_keyed.py
git commit -m "feat(search): keyed identity + delete on TaggedKVDatabase (v3)"
```

---

### Task A2: `update_tags` (no re-embed)

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Test: `tests/search/test_update_tags.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/search/test_update_tags.py
import numpy as np
from search.tagged_kvdb import TaggedKVDatabase
from tests.search.test_tagged_kvdb_keyed import FakeEmbedder


def test_update_tags_changes_filter_without_reembedding():
    emb = FakeEmbedder()
    db = TaggedKVDatabase(embedder=emb)
    db.add("vector math helper", {"id": "n1"}, tags=["@in:f1"], key="n1")
    before = db.search("vector math helper", 1)[0][1]   # similarity score
    db.update_tags("n1", {"@in:f2"})
    assert db.list_by_tags(["@in:f1"]) == []
    assert [v["id"] for v in db.list_by_tags(["@in:f2"])] == ["n1"]
    after = db.search("vector math helper", 1)[0][1]
    assert before == after            # vector untouched → identical score
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/search/test_update_tags.py -v`
Expected: FAIL — no `update_tags`.

- [ ] **Step 3: Add `update_tags` to `tagged_kvdb.py`**

```python
    def update_tags(self, key: str, tags: Iterable[str]) -> None:
        """Replace the tag set for the entry identified by `key`. Does not re-embed."""
        tag_set = _validate_tags(tags)
        with self._lock.write():
            id_ = self._store.phrase_to_id.get(key)
            if id_ is None:
                raise KeyError(key)
            self._remove_id_from_tags(id_)
            self._add_id_to_tags(id_, tag_set)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/search/test_update_tags.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/search/tagged_kvdb.py tests/search/test_update_tags.py
git commit -m "feat(search): update_tags rewrites tags without re-embedding"
```

---

### Task A3: `search_filtered` (CNF: AND-tags + OR-groups)

**Files:**
- Modify: `src/search/tagged_kvdb.py`
- Test: `tests/search/test_search_filtered.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/search/test_search_filtered.py
import numpy as np
from search.tagged_kvdb import TaggedKVDatabase


class CountingEmbedder:
    model_name = "fake"
    dim = 8

    def __init__(self):
        self.calls = 0

    def encode(self, phrase: str) -> np.ndarray:
        self.calls += 1
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def _db(emb):
    db = TaggedKVDatabase(embedder=emb)
    db.add("a", {"id": "n1"}, tags=["@kind:method", "@in:f1", "topic"], key="n1")
    db.add("b", {"id": "n2"}, tags=["@kind:class", "@in:f1", "topic"], key="n2")
    db.add("c", {"id": "n3"}, tags=["@kind:method", "@in:f2"], key="n3")
    return db


def test_and_tags_with_or_groups():
    emb = CountingEmbedder()
    db = _db(emb)
    # topic AND (in f1 OR f2) AND (method OR class)
    res = db.search_filtered("a", 10, all_tags=["topic"],
                             any_groups=[{"@in:f1", "@in:f2"}, {"@kind:method", "@kind:class"}])
    got = {v["id"] for v, _ in res}
    assert got == {"n1", "n2"}            # n3 lacks "topic"


def test_embeds_exactly_once():
    emb = CountingEmbedder()
    db = _db(emb)
    emb.calls = 0
    db.search_filtered("query", 10, all_tags=[], any_groups=[{"@in:f1"}, {"@kind:method"}])
    assert emb.calls == 1


def test_no_groups_equals_plain_search():
    emb = CountingEmbedder()
    db = _db(emb)
    a = {v["id"] for v, _ in db.search_filtered("a", 10, all_tags=["topic"])}
    b = {v["id"] for v, _ in db.search("a", 10, tags=["topic"])}
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/search/test_search_filtered.py -v`
Expected: FAIL — no `search_filtered`.

- [ ] **Step 3: Add helpers + `search_filtered` to `tagged_kvdb.py`**

```python
    def _union_tag_ids(self, tags: Iterable[str]) -> set[int]:
        """Ids having ANY of `tags`. Must be called under a lock."""
        out: set[int] = set()
        for t in tags:
            bucket = self._tag_to_ids.get(t)
            if bucket:
                out |= bucket
        return out

    def search_filtered(
        self,
        phrase: str,
        n: int,
        *,
        all_tags: Iterable[str] = (),
        any_groups: Iterable[Iterable[str]] = (),
    ) -> list[tuple[JSONValue, float]]:
        """One vector query whose candidates must contain every tag in `all_tags`
        AND, for each group in `any_groups`, at least one tag from that group.
        Embeds `phrase` exactly once."""
        vec = self._encode(phrase)
        with self._lock.read():
            allowed = self._intersect_tag_ids(all_tags)   # None when all_tags empty
            for group in any_groups:
                group_ids = self._union_tag_ids(group)
                allowed = group_ids if allowed is None else (allowed & group_ids)
                if not allowed:
                    break
            return self._search_locked(vec, n, allowed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/search/test_search_filtered.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/search/tagged_kvdb.py tests/search/test_search_filtered.py
git commit -m "feat(search): search_filtered CNF query (AND-tags + OR-groups), one embed"
```

---

## Phase B — `library` extensions

### Task B1: `Graph.iter_ids`, `iter_code_ids`, `is_build_stale` (+ deep staleness)

**Files:**
- Modify: `src/library/builder.py` (add `is_stale_with_deps`)
- Modify: `src/library/graph.py` (add three pass-throughs)
- Test: `tests/library/test_graph_staleness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/library/test_graph_staleness.py
from library import Graph, CodeNode, new_node_id


def _code(graph, name, body, deps=()):
    nid = new_node_id()
    graph.add_node(
        CodeNode(node_id=nid, name=name, description="d", dependencies=set(deps)),
        code=body, tests="",
    )
    return nid


def test_is_build_stale_tracks_self_and_deps(tmp_path):
    g = Graph.open(tmp_path)
    dep = _code(g, "dep", "def dep():\n    return 1\n")
    main = _code(g, "main", "def main():\n    return dep()\n", deps=[dep])
    g.ensure_built(main)
    assert g.is_build_stale(main) is False
    # change the dependency's code → dependent becomes stale
    node = g.get(dep)
    g.update_node(node, code="def dep():\n    return 2\n")
    assert g.is_build_stale(main) is True


def test_iter_code_ids_excludes_folders(tmp_path):
    from library import FolderNode
    g = Graph.open(tmp_path)
    fid = new_node_id()
    g.add_node(FolderNode(node_id=fid, name="f", description="d"))
    cid = _code(g, "fn", "def fn():\n    return 0\n")
    assert set(g.iter_code_ids()) == {cid}
    assert fid in set(g.iter_ids())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/library/test_graph_staleness.py -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Add `is_stale_with_deps` to `builder.py`**

```python
    def is_stale_with_deps(self, node_id: NodeId) -> bool:
        """True if the node has no manifest entry, its own code changed, or any
        direct dependency's code changed since the last build."""
        entry = self._manifest.get(node_id)
        if entry is None:
            return True
        try:
            if entry.code_hash != _sha256_text(self.cache.get_code(node_id)):
                return True
        except Exception:
            return True
        node = self.cache.get(node_id)
        if not isinstance(node, CodeNode):
            return True
        for dep_id in node.dependencies:
            recorded = entry.dep_hashes.get(dep_id)
            if recorded is None:
                return True
            try:
                if recorded != _sha256_text(self.cache.get_code(dep_id)):
                    return True
            except Exception:
                return True
        return False
```

- [ ] **Step 4: Add pass-throughs to `graph.py`**

```python
    def iter_ids(self):
        return self._store.iter_ids()

    def iter_code_ids(self):
        for nid in self._store.iter_ids():
            if isinstance(self._cache.get(nid), CodeNode):
                yield nid

    def is_build_stale(self, node_id: NodeId) -> bool:
        return self._builder.is_stale_with_deps(node_id)
```

- [ ] **Step 5: Run test + suite**

Run: `pytest tests/library/test_graph_staleness.py tests/library -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/library/builder.py src/library/graph.py tests/library/test_graph_staleness.py
git commit -m "feat(library): iter_ids/iter_code_ids/is_build_stale with deep staleness"
```

---

### Task B2: `Builder.build_trial` + `Graph.trial_run` / `discard_trial`

**Files:**
- Modify: `src/library/builder.py`
- Modify: `src/library/graph.py`
- Test: `tests/library/test_builder_trial.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/library/test_builder_trial.py
from library import Graph, CodeNode, TestStatus, new_node_id


def test_trial_run_does_not_commit(tmp_path):
    g = Graph.open(tmp_path)
    nid = new_node_id()
    # abstraction only: no code yet
    g.add_node(CodeNode(node_id=nid, name="inc", description="add one"))
    results = g.trial_run(nid, "def inc(x):\n    return x + 1\n",
                          "def test_inc():\n    assert inc(1) == 2\n")
    assert [r.status for r in results] == [TestStatus.PASSING]
    # store still has no code — trial wrote nothing canonical
    assert g.get_code(nid) == ""


def test_discard_trial_first_impl_removes_scratch(tmp_path):
    g = Graph.open(tmp_path)
    nid = new_node_id()
    g.add_node(CodeNode(node_id=nid, name="bad", description="d"))
    g.trial_run(nid, "def bad():\n    return 1\n", "def test_bad():\n    assert bad() == 2\n")
    g.discard_trial(nid)
    assert not (tmp_path / "build" / f"{nid}.py").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/library/test_builder_trial.py -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Add `build_trial` to `builder.py`**

```python
    def build_trial(self, node_id: NodeId, code_text: str, tests_text: str,
                    dependencies) -> None:
        """Materialize build/<id>.py and build/test_<id>.py from the given candidate
        text WITHOUT reading or writing the store and WITHOUT recording a manifest
        entry. Dependencies must already be built."""
        _scan_forbidden_build_imports(node_id, code_text, where="code.py")
        dep_nodes: list[CodeNode] = []
        for dep_id in sorted(dependencies):
            if not self.store.exists(dep_id):
                raise MissingDependency(node_id, dep_id)
            dep_node = self.cache.get(dep_id)
            if not isinstance(dep_node, CodeNode):
                raise BuildError(node_id, f"dependency {dep_id} is not a CodeNode")
            dep_nodes.append(dep_node)
        seen: dict[str, NodeId] = {}
        for dn in dep_nodes:
            if dn.name in seen:
                raise BuildError(node_id, f"duplicate dep symbol: {dn.name}")
            seen[dn.name] = dn.node_id
        self._build_root_init()
        node = self.cache.get(node_id)
        if not isinstance(node, CodeNode):
            raise BuildError(node_id, "only CodeNodes can be built")
        out = self._compose_built_file(dep_nodes, code_text)
        _atomic_write(self.build_root / f"{node_id}.py", out)
        if tests_text:
            _scan_forbidden_build_imports(node_id, tests_text, where="tests.py")
            test_out = self._compose_test_file(node, dep_nodes, tests_text)
            _atomic_write(self.build_root / f"test_{node_id}.py", test_out)
```

- [ ] **Step 4: Add `trial_run` + `discard_trial` to `graph.py`**

```python
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
            self._builder.ensure_built(node_id)
        else:
            self._builder.remove(node_id)
```

- [ ] **Step 5: Run test + suite**

Run: `pytest tests/library/test_builder_trial.py tests/library -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/library/builder.py src/library/graph.py tests/library/test_builder_trial.py
git commit -m "feat(library): build_trial + Graph.trial_run/discard_trial (staged commit)"
```

---

### Task B3: Warm pytest worker

**Files:**
- Modify: `src/library/config.py` (add `use_test_worker`, `test_timeout_seconds`)
- Create: `src/library/_test_worker.py`
- Modify: `src/library/runner.py` (extract `_parse_report`, add `_TestWorker`, branch in `run_tests`)
- Modify: `src/library/graph.py` (`Graph.open` passes flags to `Runner`)
- Test: `tests/library/test_test_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/library/test_test_worker.py
from library import Graph, CodeNode, TestStatus, new_node_id


def _impl(g, name, body, tests):
    nid = new_node_id()
    g.add_node(CodeNode(node_id=nid, name=name, description="d"), code=body, tests=tests)
    return nid


def test_worker_picks_up_rebuilt_code(tmp_path):
    g = Graph.open(tmp_path, use_test_worker=True)
    nid = _impl(g, "f", "def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")
    assert [r.status for r in g.run_tests(nid)] == [TestStatus.PASSING]
    # rebuild with new behavior; worker must not serve a stale module
    g.update_node(g.get(nid), code="def f():\n    return 2\n",
                  tests="def test_f():\n    assert f() == 2\n")
    assert [r.status for r in g.run_tests(nid)] == [TestStatus.PASSING]


def test_worker_and_oneshot_agree_on_failure(tmp_path):
    body, tests = "def f():\n    return 1\n", "def test_f():\n    assert f() == 99\n"
    g1 = Graph.open(tmp_path / "w", use_test_worker=True)
    n1 = _impl(g1, "f", body, tests)
    g2 = Graph.open(tmp_path / "o", use_test_worker=False)
    n2 = _impl(g2, "f", body, tests)
    assert [r.status for r in g1.run_tests(n1)] == [TestStatus.FAILING]
    assert [r.status for r in g2.run_tests(n2)] == [TestStatus.FAILING]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/library/test_test_worker.py -v`
Expected: FAIL — `Graph.open` rejects `use_test_worker`.

- [ ] **Step 3: Add config flags in `config.py`**

In `LibraryConfig` add fields (after `tokenizer_encoding`):

```python
    use_test_worker: bool = True
    test_timeout_seconds: float = 60.0
```

- [ ] **Step 4: Create the worker module `src/library/_test_worker.py`**

```python
"""Warm pytest worker. Reads one JSON request per line on stdin
({"target": "<test_file>", "report": "<json_report_path>"}), runs pytest
in-process against the target writing a json report, and replies with one
JSON line. Run as: python -m library._test_worker <store_root>
"""
import json
import sys


def _evict_build_modules() -> None:
    for name in list(sys.modules):
        if name == "build" or name.startswith("build.") or name.startswith("test_"):
            del sys.modules[name]


def main() -> None:
    store_root = sys.argv[1]
    if store_root not in sys.path:
        sys.path.insert(0, store_root)
    import pytest  # imported once; stays warm

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        _evict_build_modules()
        pytest.main([
            req["target"], "-q", "--no-header", "-p", "no:cacheprovider",
            "--json-report", f"--json-report-file={req['report']}",
            "--json-report-omit=streams,warnings,keywords",
        ])
        sys.stdout.write(json.dumps({"done": True}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Rewrite `runner.py` to support the worker**

Extract report parsing and add the worker. Replace the file body's `Runner` with:

```python
import json
import os
import select
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import library
from library.errors import BuildError
from library.ids import NodeId
from library.nodes import TestStatus


@dataclass
class TestResult:
    __test__ = False
    name: str
    status: TestStatus
    detail: str | None


def _worker_env(store_root: Path) -> dict:
    src_dir = str(Path(library.__file__).resolve().parent.parent)
    env = dict(os.environ)
    parts = [src_dir, str(store_root)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


class _TestWorker:
    """Long-lived warm pytest process. One per Runner. Respawned on crash."""

    MAX_RUNS = 100

    def __init__(self, store_root: Path, python: str, timeout: float) -> None:
        self._store_root = store_root
        self._python = python
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._runs = 0

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            [self._python, "-m", "library._test_worker", str(self._store_root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            cwd=str(self._store_root), env=_worker_env(self._store_root), bufsize=1,
        )
        self._runs = 0

    def run(self, target: Path, report: Path, node_id: NodeId) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        req = json.dumps({"target": str(target), "report": str(report)}) + "\n"
        try:
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            self._spawn()
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
        ready, _, _ = select.select([self._proc.stdout], [], [], self._timeout)
        if not ready:
            self.kill()
            raise BuildError(node_id, f"test run timed out after {self._timeout}s")
        reply = self._proc.stdout.readline()
        if not reply:
            self.kill()
            raise BuildError(node_id, "test worker died during run")
        self._runs += 1
        if self._runs >= self.MAX_RUNS:
            self.kill()  # bound pytest global-state drift

    def kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None


class Runner:
    def __init__(self, build_root: Path, python: str | None = None,
                 use_worker: bool = True, timeout: float = 60.0) -> None:
        self.build_root = build_root
        self.python = python or sys.executable
        self.use_worker = use_worker
        self.timeout = timeout
        self._worker_obj: _TestWorker | None = None

    def _worker(self) -> _TestWorker:
        if self._worker_obj is None:
            self._worker_obj = _TestWorker(self.build_root.parent, self.python, self.timeout)
        return self._worker_obj

    def run_tests(self, node_id: NodeId) -> list[TestResult]:
        target = self.build_root / f"test_{node_id}.py"
        if not target.exists():
            return []
        report_path = self.build_root / f".last_report_{node_id}.json"
        if report_path.exists():
            report_path.unlink()

        if self.use_worker:
            self._worker().run(target, report_path, node_id)
        else:
            self._run_oneshot(target, report_path, node_id)

        if not report_path.exists():
            raise BuildError(node_id, "pytest produced no JSON report")
        try:
            report = json.loads(report_path.read_text())
        finally:
            report_path.unlink(missing_ok=True)
        return self._parse_report(report, node_id)

    def _run_oneshot(self, target: Path, report_path: Path, node_id: NodeId) -> None:
        argv = [
            self.python, "-m", "pytest", str(target), "-q", "--no-header",
            "--json-report", f"--json-report-file={report_path}",
            "--json-report-omit=streams,warnings,keywords",
        ]
        cwd = self.build_root.parent
        env = _worker_env(cwd)
        subprocess.run(argv, capture_output=True, text=True, cwd=str(cwd), env=env)

    def _parse_report(self, report: dict, node_id: NodeId) -> list[TestResult]:
        for c in report.get("collectors", []):
            if c.get("outcome") == "failed":
                msg = c.get("longrepr") or "collection failed"
                raise BuildError(node_id, f"test collection failed: {msg.splitlines()[0]}")
        results: list[TestResult] = []
        for t in report.get("tests", []):
            func = t.get("nodeid", "").rsplit("::", 1)[-1]
            if not func.startswith("test_"):
                continue
            name = func[len("test_"):]
            outcome = t.get("outcome")
            if outcome == "passed":
                results.append(TestResult(name=name, status=TestStatus.PASSING, detail=None))
            elif outcome in ("failed", "error"):
                longrepr = t.get("call", {}).get("longrepr") or t.get("longrepr") or ""
                first = longrepr.splitlines()[0] if longrepr else outcome
                results.append(TestResult(name=name, status=TestStatus.FAILING, detail=first))
            else:
                results.append(TestResult(name=name, status=TestStatus.UNRUN, detail=None))
        return results
```

- [ ] **Step 6: Wire flags through `Graph.open` in `graph.py`**

Replace the `runner = Runner(...)` line in `Graph.open`:

```python
        runner = Runner(
            build_root=root / "build",
            use_worker=cfg.use_test_worker,
            timeout=cfg.test_timeout_seconds,
        )
```

- [ ] **Step 7: Run test + suite**

Run: `pytest tests/library/test_test_worker.py tests/library -q`
Expected: PASS (existing `test_runner.py` still green via the one-shot path and the worker default).

- [ ] **Step 8: Commit**

```bash
git add src/library/config.py src/library/_test_worker.py src/library/runner.py src/library/graph.py tests/library/test_test_worker.py
git commit -m "feat(library): warm pytest worker with one-shot fallback"
```

---

## Phase C — `api` package

### Task C1: `api/errors.py`

**Files:**
- Create: `src/api/__init__.py` (empty for now)
- Create: `src/api/errors.py`
- Test: `tests/api/__init__.py` (empty), `tests/api/test_errors.py`

- [ ] **Step 1: Add `api*` to packaging in `pyproject.toml`**

Change the include line to:

```toml
include = ["library*", "search*", "api*"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_errors.py
from api.errors import ApiError, ImplementationFailed, InvalidMove


def test_implementation_failed_carries_results():
    e = ImplementationFailed("n1", results=[], detail="boom")
    assert isinstance(e, ApiError)
    assert e.node_id == "n1" and e.detail == "boom" and e.results == []


def test_invalid_move_reason():
    e = InvalidMove("n1", "n2", "into-own-subtree")
    assert isinstance(e, ApiError)
    assert "n1" in str(e) and "into-own-subtree" in str(e)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/api/test_errors.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Create `src/api/__init__.py` (empty) and `src/api/errors.py`**

```python
# src/api/errors.py
from dataclasses import dataclass, field


class ApiError(Exception):
    """Base class for api-layer errors (also raised for a corrupt multi-root tree)."""


@dataclass
class ImplementationFailed(ApiError):
    node_id: str
    results: list = field(default_factory=list)   # list[library.TestResult]
    detail: str = ""

    def __str__(self) -> str:
        return f"implementation failed for {self.node_id}: {self.detail}"


@dataclass
class InvalidMove(ApiError):
    node_id: str
    target_id: str
    reason: str

    def __str__(self) -> str:
        return f"cannot move {self.node_id} -> {self.target_id}: {self.reason}"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/api/test_errors.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/api/__init__.py src/api/errors.py tests/api/__init__.py tests/api/test_errors.py
git commit -m "feat(api): errors module + package scaffold"
```

---

### Task C2: `api/results.py` — hits, pages, reports + rendering

**Files:**
- Create: `src/api/results.py`
- Test: `tests/api/test_results.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_results.py
from api.results import SearchHit, SearchPage, TagHit, TagPage


def _hit(i):
    return SearchHit(node_id=f"n{i}", name=f"fn{i}", kind="method",
                     description="x" * 200, score=0.9)


def test_search_page_render_has_nav_and_ids():
    hits = [_hit(1), _hit(2)]
    page = SearchPage(hits=hits, page=0, num_pages=3, total=25, page_size=2, query="q")
    text = page.render()
    assert "page 1/3" in text and "of 25" in text
    assert "n1" in text and "n2" in text
    assert "method" in text
    assert "x" * 200 not in text          # description truncated
    assert str(page) == text


def test_empty_search_page_renders():
    page = SearchPage(hits=[], page=0, num_pages=0, total=0, page_size=10, query="q")
    assert "of 0" in page.render()


def test_tag_page_render():
    page = TagPage(hits=[TagHit("statistics", 0.82)], page=0, num_pages=1, total=1,
                   page_size=10, query="stats")
    assert "statistics" in page.render() and "0.82" in page.render()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_results.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/api/results.py`**

```python
from dataclasses import dataclass, field

_DESC_WIDTH = 80


def _trunc(text: str) -> str:
    s = " ".join(text.split())
    return s if len(s) <= _DESC_WIDTH else s[: _DESC_WIDTH - 3] + "..."


@dataclass(frozen=True)
class SearchHit:
    node_id: str
    name: str
    kind: str
    description: str
    score: float


@dataclass(frozen=True)
class TagHit:
    tag: str
    score: float


@dataclass(frozen=True)
class SearchPage:
    hits: list
    page: int
    num_pages: int
    total: int
    page_size: int
    query: str

    def render(self) -> str:
        start = self.page * self.page_size + 1 if self.hits else 0
        end = start + len(self.hits) - 1 if self.hits else 0
        header = (f'query: "{self.query}"  ·  page {self.page + 1}/{max(self.num_pages, 1)}'
                  f'  ·  showing {start}–{end} of {self.total}')
        lines = [header]
        for i, h in enumerate(self.hits, start=start):
            lines.append(f"  {i}. {h.kind:<10} {h.name:<18} [{h.node_id}]  {_trunc(h.description)}")
        if self.page + 1 < self.num_pages:
            lines.append(f"  (next page: page={self.page + 1})")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True)
class TagPage:
    hits: list
    page: int
    num_pages: int
    total: int
    page_size: int
    query: str

    def render(self) -> str:
        start = self.page * self.page_size + 1 if self.hits else 0
        end = start + len(self.hits) - 1 if self.hits else 0
        lines = [f'query: "{self.query}"  ·  page {self.page + 1}/{max(self.num_pages, 1)}'
                 f'  ·  showing {start}–{end} of {self.total}']
        for i, h in enumerate(self.hits, start=start):
            lines.append(f"  {i}. {h.tag:<20} ({h.score:.2f})")
        if self.page + 1 < self.num_pages:
            lines.append(f"  (next page: page={self.page + 1})")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


@dataclass
class ImplementResult:
    node_id: str
    results: list
    all_passing: bool


@dataclass
class RebuildReport:
    rebuilt: list = field(default_factory=list)
    passed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_results.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/results.py tests/api/test_results.py
git commit -m "feat(api): result/page dataclasses with rendered text"
```

---

### Task C3: `SearchSystem` — index + filtered search + tags

**Files:**
- Create: `src/api/search_system.py`
- Test: `tests/api/test_search_system.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_search_system.py
import numpy as np
import pytest
from api.search_system import SearchSystem


class FakeEmbedder:
    model_name = "fake"
    dim = 8

    def encode(self, phrase: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def _ss(tmp_path):
    return SearchSystem.open(tmp_path / "index", embedder=FakeEmbedder())


def test_index_search_and_remove(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "rolling_mean", "streaming mean", "method",
                  {"stats", "@kind:method", "@in:f1"})
    ss.index_node("n2", "RingBuffer", "circular buffer", "class",
                  {"@kind:class", "@in:f1"})
    hits = ss.search("mean", n=10, tags={"stats"})
    assert [h.node_id for h in hits] == ["n1"]
    ss.remove_node("n1")
    assert ss.search("mean", n=10, tags={"stats"}) == []


def test_duplicate_descriptions_both_indexed(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "same text", "method", {"@kind:method", "@in:f1"})
    ss.index_node("n2", "b", "same text", "method", {"@kind:method", "@in:f2"})
    f1 = {h.node_id for h in ss.search("same", n=10, folders={"f1"})}
    f2 = {h.node_id for h in ss.search("same", n=10, folders={"f2"})}
    assert f1 == {"n1"} and f2 == {"n2"}


def test_folder_and_type_or_semantics(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "d", "method", {"@kind:method", "@in:f1"})
    ss.index_node("n2", "b", "d", "class", {"@kind:class", "@in:f2"})
    ss.index_node("n3", "c", "d", "method", {"@kind:method", "@in:f3"})
    got = {h.node_id for h in ss.search("d", n=10, folders={"f1", "f2"})}
    assert got == {"n1", "n2"}
    got = {h.node_id for h in ss.search("d", n=10, object_types={"class", "method"}, folders={"f1", "f2"})}
    assert got == {"n1", "n2"}


def test_list_tags_excludes_synthetics(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "d", "method", {"stats", "@kind:method", "@in:f1"})
    assert ss.list_tags() == {"stats"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_search_system.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/api/search_system.py` (indexing + search; paging added in C4)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_search_system.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/search_system.py tests/api/test_search_system.py
git commit -m "feat(api): SearchSystem index + composite-tag filtered search"
```

---

### Task C4: `SearchSystem` paging + LRU cache

**Files:**
- Modify: `src/api/search_system.py`
- Test: `tests/api/test_search_paging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_search_paging.py
import numpy as np
import pytest
from api.search_system import SearchSystem
from tests.api.test_search_system import FakeEmbedder


class Counting(FakeEmbedder):
    def __init__(self):
        self.calls = 0

    def encode(self, phrase):
        self.calls += 1
        return super().encode(phrase)


def _ss(tmp_path, emb):
    ss = SearchSystem.open(tmp_path / "index", embedder=emb)
    for i in range(25):
        ss.index_node(f"n{i}", f"fn{i}", f"item {i}", "method", {"@kind:method", "@in:f1"})
    return ss


def test_paging_slices_and_counts(tmp_path):
    ss = _ss(tmp_path, FakeEmbedder())
    p0 = ss.search_page("item", page=0, page_size=10)
    assert len(p0.hits) == 10 and p0.total == 25 and p0.num_pages == 3
    p2 = ss.search_page("item", page=2, page_size=10)
    assert len(p2.hits) == 5


def test_page_flip_embeds_once(tmp_path):
    emb = Counting()
    ss = _ss(tmp_path, emb)
    emb.calls = 0
    ss.search_page("item", page=0, page_size=10)
    ss.search_page("item", page=1, page_size=10)
    ss.search_page("item", page=2, page_size=10)
    assert emb.calls == 1                       # cached PagedList


def test_out_of_range_and_empty(tmp_path):
    ss = _ss(tmp_path, FakeEmbedder())
    with pytest.raises(IndexError):
        ss.search_page("item", page=99, page_size=10)
    empty = ss.search_page("item", page=0, page_size=10, tags={"nope"})
    assert empty.hits == [] and empty.num_pages == 0


def test_mutation_clears_cache(tmp_path):
    emb = Counting()
    ss = _ss(tmp_path, emb)
    ss.search_page("item", page=0, page_size=10)
    emb.calls = 0
    ss.index_node("n99", "fn99", "item 99", "method", {"@kind:method", "@in:f1"})
    ss.search_page("item", page=0, page_size=10)
    assert emb.calls == 1                       # cache cleared → re-embedded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_search_paging.py -v`
Expected: FAIL — no `search_page`.

- [ ] **Step 3: Add the cache + paged methods to `search_system.py`**

Add imports at the top:

```python
from collections import OrderedDict

from search.pages import Page, PagedList
from api.results import SearchPage, TagPage
```

In `__init__`, initialise the cache:

```python
        self._cache: "OrderedDict[tuple, PagedList]" = OrderedDict()
        self._cache_cap = 16
```

Add a cache helper and have every mutator clear it. Add a private clear call at the end of `index_node`, `remove_node`, `update_tags`, `index_tags`, `reindex`:

```python
        self._cache.clear()
```

Add the paged query methods:

```python
    def _cache_get(self, key):
        plist = self._cache.get(key)
        if plist is not None:
            self._cache.move_to_end(key)
        return plist

    def _cache_put(self, key, plist) -> None:
        self._cache[key] = plist
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_cap:
            self._cache.popitem(last=False)

    @staticmethod
    def _page(plist: PagedList, page: int, page_size: int, query: str, mk):
        num_pages = plist.num_pages
        if num_pages == 0:
            hits = []
        else:
            hits = list(plist.get_page(page))   # IndexError if out of range
        return mk(hits=hits, page=page, num_pages=num_pages,
                  total=len(plist), page_size=page_size, query=query)

    def search_page(self, query: str, *, page: int = 0, page_size: int = 10,
                    tags: set[str] = frozenset(), object_types: set[str] = frozenset(),
                    folders: set[str] = frozenset()) -> SearchPage:
        key = ("nodes", query, frozenset(tags), frozenset(object_types),
               frozenset(folders), page_size)
        plist = self._cache_get(key)
        if plist is None:
            raw = self._nodes.search_filtered(
                query, len(self._nodes), all_tags=set(tags),
                any_groups=self._any_groups(set(object_types), set(folders)),
            )
            plist = PagedList([self._hit(v, s) for v, s in raw], page_size)
            self._cache_put(key, plist)
        return self._page(plist, page, page_size, query, SearchPage)

    def search_tags_page(self, query: str, *, page: int = 0,
                         page_size: int = 10) -> TagPage:
        key = ("tags", query, page_size)
        plist = self._cache_get(key)
        if plist is None:
            raw = self._tags.search(query, max(len(self._tags), 1))
            plist = PagedList([TagHit(v, s) for v, s in raw], page_size)
            self._cache_put(key, plist)
        return self._page(plist, page, page_size, query, TagPage)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_search_paging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/search_system.py tests/api/test_search_paging.py
git commit -m "feat(api): paged search with cached PagedList, cleared on mutation"
```

---

### Task C5: `Codebase` — open, root bootstrap, indexing helpers

**Files:**
- Create: `src/api/codebase.py`
- Test: `tests/api/test_codebase_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_codebase_bootstrap.py
import numpy as np
import pytest
from api.codebase import Codebase
from api.errors import ApiError
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_first_open_creates_single_root(tmp_path):
    cb = _open(tmp_path)
    rid = cb.root_id
    assert rid
    node = cb.load(rid)
    assert node.parent_id is None and node.name == "root"


def test_reopen_reuses_root(tmp_path):
    cb1 = _open(tmp_path)
    rid = cb1.root_id
    cb2 = _open(tmp_path)
    assert cb2.root_id == rid


def test_reindex_when_index_missing(tmp_path):
    cb = _open(tmp_path)
    fid = cb.make_folder("utils")
    import shutil
    shutil.rmtree(tmp_path / "index")
    cb2 = _open(tmp_path)
    hits = cb2.search("utils", folders=()).hits
    assert any(h.node_id == fid for h in hits)
```

`Codebase.open` must accept an `embedder` kwarg (passed to `SearchSystem`); other kwargs go to `Graph.open` config overrides.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_codebase_bootstrap.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/api/codebase.py` (lifecycle + helpers; methods added in C6–C9)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_codebase_bootstrap.py -v`
Expected: PASS (note: this also exercises `make_folder`/`search` added next; if running C5 alone, run only `test_first_open_creates_single_root` and `test_reopen_reuses_root`, then re-run the full file after C6/C9).

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_bootstrap.py
git commit -m "feat(api): Codebase open + root bootstrap + reindex-from-store"
```

---

### Task C6: `Codebase` folders — make_folder, move, rename, remove

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_folders.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_codebase_folders.py
import pytest
from api.codebase import Codebase
from api.errors import InvalidMove
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_make_folder_nests_and_indexes(tmp_path):
    cb = _open(tmp_path)
    a = cb.make_folder("a")
    b = cb.make_folder("b", parent_id=a)
    assert b in cb.children_of(a)
    assert any(h.node_id == b for h in cb.search("b", folders={a}).hits)


def test_move_retags_subtree(tmp_path):
    cb = _open(tmp_path)
    a = cb.make_folder("a")
    b = cb.make_folder("b")
    leaf = cb.make_folder("leaf", parent_id=a)
    # leaf is under a → matches folders={a}
    assert {h.node_id for h in cb.search("leaf", folders={a}).hits} == {leaf}
    cb.move(a, b)                       # move a (and its subtree) under b
    # leaf is now under b (transitively) and still under a (a was moved, not deleted)
    assert {h.node_id for h in cb.search("leaf", folders={b}).hits} == {leaf}
    assert {h.node_id for h in cb.search("leaf", folders={a}).hits} == {leaf}


def test_move_into_own_subtree_rejected(tmp_path):
    cb = _open(tmp_path)
    a = cb.make_folder("a")
    child = cb.make_folder("child", parent_id=a)
    with pytest.raises(InvalidMove):
        cb.move(a, child)
    with pytest.raises(InvalidMove):
        cb.move(cb.root_id, a)          # cannot move root
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_codebase_folders.py -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Add subtree helper + folder methods to `codebase.py`**

```python
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

    def make_folder(self, name, *, parent_id=None, description="", tags=()) -> str:
        parent_id = parent_id or self._root_id
        if not isinstance(self._graph.get(parent_id), FolderNode):
            raise InvalidMove(parent_id, parent_id, "target-not-folder")
        nid = new_node_id()
        folder = FolderNode(node_id=nid, name=name, description=description,
                            parent_id=parent_id, tags=self._tagset(tags))
        self._graph.add_node(folder)
        self._attach_to_parent(nid, parent_id)
        self._index_node(folder)
        return nid

    def _attach_to_parent(self, node_id: str, parent_id: str) -> None:
        parent = self._graph.get(parent_id)
        parent.children.add(node_id)
        self._graph.update_node(parent)

    def _detach_from_parent(self, node_id: str, parent_id: str) -> None:
        parent = self._graph.get(parent_id)
        parent.children.discard(node_id)
        self._graph.update_node(parent)

    def move(self, node_id, new_parent_id) -> None:
        if node_id == self._root_id:
            raise InvalidMove(node_id, new_parent_id, "move-root")
        if not isinstance(self._graph.get(new_parent_id), FolderNode):
            raise InvalidMove(node_id, new_parent_id, "target-not-folder")
        if new_parent_id == node_id or new_parent_id in self._subtree_ids(node_id):
            raise InvalidMove(node_id, new_parent_id, "into-own-subtree")
        node = self._graph.get(node_id)
        old_parent = node.parent_id
        if old_parent is not None:
            self._detach_from_parent(node_id, old_parent)
        node.parent_id = new_parent_id
        self._graph.update_node(node)
        self._attach_to_parent(node_id, new_parent_id)
        # re-tag moved node + descendants (their @in: ancestry changed); no re-embed
        for nid in [node_id, *self._subtree_ids(node_id)]:
            self._search.update_tags(nid, self._composite_tags(self._graph.get(nid)))

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_codebase_folders.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_folders.py
git commit -m "feat(api): folders — make_folder/move(retag subtree)/rename/remove"
```

---

### Task C7: `Codebase` — define_abstraction + add_* sugar

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_define.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_codebase_define.py
from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_define_abstraction_is_searchable_and_dirty(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("rolling_mean", "streaming mean over a window", tags=["stats"])
    assert nid in cb.dirty()                         # no code yet
    assert cb.load_code(nid) == ""
    hits = cb.search("mean", tags={"stats"}, object_types={"method"}).hits
    assert any(h.node_id == nid for h in hits)
    assert "stats" in cb.list_tags()


def test_add_class_and_executable_kinds(tmp_path):
    cb = _open(tmp_path)
    c = cb.add_class("RingBuffer", "circular buffer")
    e = cb.add_executable("main", "entrypoint")
    assert cb.load(c).object_type == "class"
    assert cb.load(e).object_type == "executable"
```

This depends on `dirty()` (Task C8). Add a temporary minimal `dirty` now or run only the search assertions; the full file passes after C8. (Implement `dirty` here if executing strictly in order — see C8 Step 3 code.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_codebase_define.py -v`
Expected: FAIL — `add_method` missing.

- [ ] **Step 3: Add define + sugar to `codebase.py`**

```python
    def define_abstraction(self, name, description, object_type, *,
                           parent_id=None, dependencies=(), tags=()) -> str:
        parent_id = parent_id or self._root_id
        if not isinstance(self._graph.get(parent_id), FolderNode):
            raise InvalidMove(parent_id, parent_id, "target-not-folder")
        nid = new_node_id()
        node = CodeNode(node_id=nid, name=name, description=description,
                        parent_id=parent_id, tags=self._tagset(tags),
                        dependencies=set(dependencies), object_type=object_type, tests=[])
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_codebase_define.py -v` (after C8's `dirty` exists)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_define.py
git commit -m "feat(api): define_abstraction + add_method/class/executable"
```

---

### Task C8: `Codebase` — dirty() + rebuild()

**Files:**
- Modify: `src/api/codebase.py`
- Test: `tests/api/test_codebase_rebuild.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_codebase_rebuild.py
from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_rebuild_reports_failures_for_unimplemented(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("f", "does f")          # abstraction only → dirty, unbuildable
    report = cb.rebuild()
    assert nid in report.failed
    assert nid in cb.dirty()


def test_rebuild_after_implement_is_clean(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_inc():\n    assert inc(1) == 2\n")
    assert nid not in cb.dirty()
    report = cb.rebuild()
    assert nid in report.skipped or report.passed == []   # nothing dirty to redo
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_codebase_rebuild.py -v`
Expected: FAIL — `dirty`/`rebuild`/`implement` missing (implement lands in C9; this file fully passes after C9).

- [ ] **Step 3: Add `dirty`, topo helper, and `rebuild` to `codebase.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_codebase_rebuild.py -v` (after C9)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/codebase.py tests/api/test_codebase_rebuild.py
git commit -m "feat(api): derived dirty() + incremental rebuild()"
```

---

### Task C9: `Codebase` — implement (staged atomic commit) + search wiring + `__init__`

**Files:**
- Modify: `src/api/codebase.py`
- Modify: `src/api/__init__.py`
- Test: `tests/api/test_codebase_implement.py`, `tests/api/test_codebase_search.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_codebase_implement.py
import pytest
from api.codebase import Codebase
from api.errors import ImplementationFailed
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_implement_commits_on_pass(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n",
                       "def test_inc():\n    assert inc(1) == 2\n")
    assert res.all_passing
    assert cb.load_code(nid) == "def inc(x):\n    return x + 1\n"
    assert nid not in cb.dirty()


def test_implement_failure_leaves_node_untouched(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("bad", "broken")
    with pytest.raises(ImplementationFailed) as ei:
        cb.implement(nid, "def bad():\n    return 1\n",
                     "def test_bad():\n    assert bad() == 2\n")
    assert ei.value.results                      # carries per-test results
    assert cb.load_code(nid) == ""               # never wrote unvalidated code
    assert nid in cb.dirty()


def test_reimplement_preserves_prior_until_green(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_inc():\n    assert inc(1) == 2\n")
    with pytest.raises(ImplementationFailed):
        cb.implement(nid, "def inc(x):\n    return x + 5\n",
                     "def test_inc():\n    assert inc(1) == 2\n")
    assert cb.load_code(nid) == "def inc(x):\n    return x + 1\n"   # prior intact
```

```python
# tests/api/test_codebase_search.py
from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_discover_then_filter_loop(tmp_path):
    cb = _open(tmp_path)
    io = cb.make_folder("io")
    m = cb.add_method("read_csv", "read a csv file", parent_id=io, tags=["parsing"])
    cb.add_class("Buffer", "a buffer", tags=["memory"])
    # filter by tag + folder + type, paged
    page = cb.search("csv", tags={"parsing"}, folders={io}, object_types={"method"})
    assert [h.node_id for h in page.hits] == [m]
    assert "read_csv" in page.render()
    # tag discovery
    tags_page = cb.search_tags("parse text")
    assert any(h.tag == "parsing" for h in tags_page.hits)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/test_codebase_implement.py tests/api/test_codebase_search.py -v`
Expected: FAIL — `implement`/`search`/`search_tags` missing.

- [ ] **Step 3: Add implement + search wiring to `codebase.py`**

```python
    def implement(self, node_id, code, tests) -> ImplementResult:
        node = self._graph.get(node_id)
        if not isinstance(node, CodeNode):
            raise ApiError(f"{node_id} is not a code node")
        results = self._graph.trial_run(node_id, code, tests)
        passing = bool(results) and all(r.status == TestStatus.PASSING for r in results)
        if not passing:
            self._graph.discard_trial(node_id)
            detail = next((r.detail for r in results if r.detail), None) or (
                "no tests defined" if not results else "tests failed")
            raise ImplementationFailed(node_id, results=results, detail=detail)
        # commit: write validated code/tests, record test names, materialize canonically
        node.tests = [Test(name=r.name, status=r.status) for r in results]
        self._graph.update_node(node, code=code, tests=tests)
        self._graph.ensure_built(node_id)
        return ImplementResult(node_id=node_id, results=results, all_passing=True)

    def search(self, query, *, page=0, page_size=10, tags=(), folders=(),
               object_types=()) -> SearchPage:
        return self._search.search_page(
            query, page=page, page_size=page_size, tags=set(tags),
            object_types=set(object_types), folders=set(folders))

    def search_tags(self, query, *, page=0, page_size=10) -> TagPage:
        return self._search.search_tags_page(query, page=page, page_size=page_size)
```

- [ ] **Step 4: Populate `src/api/__init__.py`**

```python
from api.codebase import Codebase
from api.errors import ApiError, ImplementationFailed, InvalidMove
from api.results import (
    ImplementResult, RebuildReport, SearchHit, SearchPage, TagHit, TagPage,
)
from api.search_system import SearchSystem

__all__ = [
    "ApiError", "Codebase", "ImplementResult", "ImplementationFailed",
    "InvalidMove", "RebuildReport", "SearchHit", "SearchPage", "SearchSystem",
    "TagHit", "TagPage",
]
```

- [ ] **Step 5: Run the whole api + library + search suite**

Run: `pytest tests/api tests/library tests/search -q`
Expected: PASS. Also re-run the earlier files that depended on later tasks:
`pytest tests/api/test_codebase_bootstrap.py tests/api/test_codebase_define.py tests/api/test_codebase_rebuild.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/codebase.py src/api/__init__.py tests/api/test_codebase_implement.py tests/api/test_codebase_search.py
git commit -m "feat(api): staged-commit implement + search wiring + package exports"
```

---

## Final verification

- [ ] **Run the complete suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Smoke test the end-to-end loop manually**

```bash
python -c "
from pathlib import Path; import tempfile
from api import Codebase
d = tempfile.mkdtemp()
cb = Codebase.open(Path(d))
io = cb.make_folder('io')
nid = cb.add_method('inc', 'add one to x', parent_id=io, tags=['math'])
res = cb.implement(nid, 'def inc(x):\n    return x + 1\n', 'def test_inc():\n    assert inc(1) == 2\n')
print('passing:', res.all_passing, 'dirty:', cb.dirty())
print(cb.search('increment', tags={'math'}, folders={io}, object_types={'method'}).render())
"
```
Expected: `passing: True dirty: set()` then a rendered search page listing `inc`.

---

## Self-review notes (addressed)

- **Spec coverage:** (a) root bootstrap → C5; (b) define/implement staged commit → C7/C9 (+ B2); (c) folders/move → C6; (d) incremental rebuild → C8 (+ B1); (e) tag/vector search with tag-AND, folder/type-OR, paged rendered hits → A3/C3/C4/C9; warm worker → B3. Search-store v3 additions → A1–A3.
- **Type consistency:** `Codebase.search/search_tags` return `SearchPage`/`TagPage`; `SearchSystem.search_page/search_tags_page` build them via `_page`. `implement` returns `ImplementResult`; failure raises `ImplementationFailed(node_id, results, detail)`. Composite tags use `@kind:`/`@in:` consistently in `_composite_tags`, `_any_groups`, and tests.
- **No placeholders:** every code step contains full code; tests that depend on later tasks are flagged with the order in which the file goes fully green.
