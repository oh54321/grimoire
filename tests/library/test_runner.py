from pathlib import Path

import pytest

from grimoire.library.builder import Builder
from grimoire.library.cache import NodeCache
from grimoire.library.config import LibraryConfig
from grimoire.library.errors import BuildError
from grimoire.library.nodes import CodeNode, Test
from grimoire.library.runner import Runner, TestResult
from grimoire.library.nodes import TestStatus
from grimoire.library.store import NodeStore


def _wire(tmp_path: Path) -> tuple[NodeStore, NodeCache, Builder, Runner]:
    cfg = LibraryConfig(root_path=tmp_path, max_description_tokens=10_000)
    store = NodeStore(cfg)
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)
    builder = Builder(store, cache, build_root=tmp_path / "build")
    runner = Runner(build_root=tmp_path / "build")
    return store, cache, builder, runner


def test_no_tests_returns_empty_list(tmp_path: Path):
    store, _, builder, runner = _wire(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): return 1\n")
    builder.ensure_built("a")
    assert runner.run_tests("a") == []


def test_passing_test_reports_passing(tmp_path: Path):
    store, _, builder, runner = _wire(tmp_path)
    store.save(
        CodeNode(node_id="a", name="add_one", description="x", tests=[Test(name="basic")]),
        code="def add_one(x): return x + 1\n",
        tests="def test_basic(): assert add_one(0) == 1\n",
    )
    builder.ensure_built("a")
    results = runner.run_tests("a")
    assert len(results) == 1
    assert results[0].name == "basic"
    assert results[0].status is TestStatus.PASSING
    assert results[0].detail is None


def test_failing_test_reports_failing_with_detail(tmp_path: Path):
    store, _, builder, runner = _wire(tmp_path)
    store.save(
        CodeNode(node_id="a", name="add_one", description="x", tests=[Test(name="wrong")]),
        code="def add_one(x): return x + 1\n",
        tests="def test_wrong(): assert add_one(0) == 99\n",
    )
    builder.ensure_built("a")
    results = runner.run_tests("a")
    assert len(results) == 1
    assert results[0].status is TestStatus.FAILING
    assert results[0].detail is not None
    assert len(results[0].detail) > 0


def test_collection_error_raises_build_error(tmp_path: Path):
    store, _, builder, runner = _wire(tmp_path)
    store.save(
        CodeNode(node_id="a", name="f", description="x", tests=[Test(name="x")]),
        code="def f(): pass\n",
        tests="import definitely_not_a_real_module_xyz123\n\ndef test_x(): pass\n",
    )
    builder.ensure_built("a")
    with pytest.raises(BuildError):
        runner.run_tests("a")
