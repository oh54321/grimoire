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
