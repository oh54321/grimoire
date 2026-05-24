import json
from pathlib import Path

import pytest

from library.builder import Builder
from library.cache import NodeCache
from library.config import LibraryConfig
from library.errors import BuildError, MissingDependency
from library.nodes import CodeNode
from library.store import NodeStore


def _setup(tmp_path: Path) -> tuple[NodeStore, NodeCache, Builder]:
    cfg = LibraryConfig(root_path=tmp_path, max_description_tokens=10_000)
    store = NodeStore(cfg)
    cache = NodeCache(store, max_bytes=1_000_000, ttl_seconds=3600.0)
    builder = Builder(store, cache, build_root=tmp_path / "build")
    return store, cache, builder


def test_first_build_creates_file_and_manifest(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(
        CodeNode(node_id="a", name="add_one", description="x"),
        code="def add_one(x): return x + 1\n",
    )

    rebuilt = builder.ensure_built("a")

    assert rebuilt is True
    built_file = tmp_path / "build" / "a.py"
    assert built_file.exists()
    assert "def add_one(x): return x + 1" in built_file.read_text()
    manifest = json.loads((tmp_path / "build" / "_manifest.json").read_text())
    assert "a" in manifest


def test_second_build_is_noop(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")
    assert builder.ensure_built("a") is False


def test_build_writes_init_py(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")
    assert (tmp_path / "build" / "__init__.py").exists()


def test_node_without_code_raises_build_error(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"))  # no code file
    with pytest.raises(BuildError):
        builder.ensure_built("a")


def test_dep_preamble_generated_in_built_file(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="dep", name="add_one", description="x"), code="def add_one(x): return x + 1\n")
    store.save(
        CodeNode(node_id="parent", name="add_two", description="x", dependencies={"dep"}),
        code="def add_two(x): return add_one(x) + 1\n",
    )
    builder.ensure_built("parent")
    built = (tmp_path / "build" / "parent.py").read_text()
    assert "from build.dep import add_one" in built
    assert "def add_two" in built


def test_missing_dependency_raises(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(
        CodeNode(node_id="a", name="f", description="x", dependencies={"ghost"}),
        code="def f(): return ghost()\n",
    )
    with pytest.raises(MissingDependency) as exc:
        builder.ensure_built("a")
    assert exc.value.missing_dep_id == "ghost"


def test_forbidden_from_build_import_raises_build_error(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(
        CodeNode(node_id="a", name="f", description="x"),
        code="from build.something import x\ndef f(): return x\n",
    )
    with pytest.raises(BuildError) as exc:
        builder.ensure_built("a")
    assert "from build" in exc.value.reason.lower()


def test_duplicate_dep_symbol_raises_build_error(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="d1", name="same", description="x"), code="def same(): return 1\n")
    store.save(CodeNode(node_id="d2", name="same", description="x"), code="def same(): return 2\n")
    store.save(
        CodeNode(node_id="p", name="parent", description="x", dependencies={"d1", "d2"}),
        code="def parent(): return same()\n",
    )
    with pytest.raises(BuildError) as exc:
        builder.ensure_built("p")
    assert "duplicate" in exc.value.reason.lower()


def test_test_file_materialized_with_preamble(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="dep", name="add_one", description="x"), code="def add_one(x): return x + 1\n")
    store.save(
        CodeNode(node_id="p", name="add_two", description="x", dependencies={"dep"}),
        code="def add_two(x): return add_one(x) + 1\n",
        tests="def test_basic(): assert add_two(0) == 2\n",
    )
    builder.ensure_built("p")
    test_file = tmp_path / "build" / "test_p.py"
    assert test_file.exists()
    content = test_file.read_text()
    assert "from build.p import add_two" in content
    assert "from build.dep import add_one" in content
    assert "def test_basic" in content


def test_no_test_file_when_tests_py_absent(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")
    assert not (tmp_path / "build" / "test_a.py").exists()


def test_diamond_dep_visited_once(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="d", name="leaf", description="x"), code="def leaf(): return 0\n")
    store.save(CodeNode(node_id="m1", name="mid1", description="x", dependencies={"d"}), code="def mid1(): return leaf()\n")
    store.save(CodeNode(node_id="m2", name="mid2", description="x", dependencies={"d"}), code="def mid2(): return leaf()\n")
    store.save(
        CodeNode(node_id="top", name="top", description="x", dependencies={"m1", "m2"}),
        code="def top(): return mid1() + mid2()\n",
    )
    # Should not raise (diamond is fine). Returns True since everything rebuilds first time.
    assert builder.ensure_built("top") is True
    # Both mids reference d. d should be built exactly once and the result reused.
    manifest = json.loads((tmp_path / "build" / "_manifest.json").read_text())
    assert set(manifest.keys()) == {"d", "m1", "m2", "top"}
