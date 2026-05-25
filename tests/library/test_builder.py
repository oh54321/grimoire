import json
from pathlib import Path

import pytest

from grimoire.library.builder import Builder
from grimoire.library.cache import NodeCache
from grimoire.library.config import LibraryConfig
from grimoire.library.errors import BuildError, MissingDependency
from grimoire.library.nodes import CodeNode
from grimoire.library.store import NodeStore


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


def test_unimplemented_dependency_gives_clear_error(tmp_path: Path):
    """Depending on a node that was define'd but never implement'ed must fail
    with a message that names the cause, not a cryptic one."""
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="dep", name="helper", description="x"))  # no code
    store.save(
        CodeNode(node_id="p", name="parent", description="x", dependencies={"dep"}),
        code="def parent(): return helper()\n",
    )
    with pytest.raises(BuildError) as exc:
        builder.ensure_built("p")
    assert "implement" in exc.value.reason.lower()


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


def test_updating_dep_invalidates_dependent(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="d", name="leaf", description="x"), code="def leaf(): return 1\n")
    store.save(
        CodeNode(node_id="p", name="top", description="x", dependencies={"d"}),
        code="def top(): return leaf()\n",
    )
    builder.ensure_built("p")
    assert builder.ensure_built("p") is False  # nothing changed

    # Mutate the dep's code on disk via the store (simulating Graph.update_node + invalidate)
    store.save(CodeNode(node_id="d", name="leaf", description="x"), code="def leaf(): return 2\n")
    # The cache still holds the old code; invalidate it so cache.get_code re-reads from disk.
    builder.cache.invalidate("d")

    # Dep's manifest still claims old hash; but `_ensure_built(d)` will rebuild because
    # its file content differs, then propagate up to p.
    assert builder.ensure_built("p") is True


def test_invalidate_drops_manifest_entry(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")
    builder.invalidate("a")
    manifest = json.loads((tmp_path / "build" / "_manifest.json").read_text())
    assert "a" not in manifest


def test_remove_drops_manifest_and_files(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n", tests="def test_x(): pass\n")
    builder.ensure_built("a")
    assert (tmp_path / "build" / "a.py").exists()
    assert (tmp_path / "build" / "test_a.py").exists()

    builder.remove("a")
    assert not (tmp_path / "build" / "a.py").exists()
    assert not (tmp_path / "build" / "test_a.py").exists()
    manifest = json.loads((tmp_path / "build" / "_manifest.json").read_text())
    assert "a" not in manifest


def test_clean_wipes_build_root(tmp_path: Path):
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")
    assert (tmp_path / "build").exists()

    builder.clean()
    assert not (tmp_path / "build").exists()
    # After clean, ensure_built must rebuild everything.
    assert builder.ensure_built("a") is True


def test_manifest_persists_across_builder_instances(tmp_path: Path):
    store, cache, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="a", name="f", description="x"), code="def f(): pass\n")
    builder.ensure_built("a")

    # Create a fresh Builder (simulating process restart); it should load the manifest.
    builder2 = Builder(store, cache, build_root=tmp_path / "build")
    assert builder2.ensure_built("a") is False


def test_future_import_hoisted_above_preamble_in_built_file(tmp_path: Path):
    """A node with deps whose code begins with `from __future__ import annotations`
    must still compile: the future import has to be hoisted above the generated
    import preamble (future imports must be the first statement in a module)."""
    store, _, builder = _setup(tmp_path)
    store.save(CodeNode(node_id="dep", name="add_one", description="x"),
               code="def add_one(x): return x + 1\n")
    store.save(
        CodeNode(node_id="parent", name="add_two", description="x", dependencies={"dep"}),
        code="from __future__ import annotations\n\ndef add_two(x): return add_one(x) + 1\n",
    )
    builder.ensure_built("parent")
    built = (tmp_path / "build" / "parent.py").read_text()
    compile(built, "parent.py", "exec")  # must not raise SyntaxError
    assert built.index("from __future__") < built.index("AUTO-GENERATED")


def test_future_import_in_tests_hoisted(tmp_path: Path):
    """The test file always gets an import preamble, so a node whose tests begin
    with `from __future__ import annotations` must still compile."""
    store, _, builder = _setup(tmp_path)
    store.save(
        CodeNode(node_id="a", name="f", description="x"),
        code="def f(): return 1\n",
        tests="from __future__ import annotations\n\ndef test_f(): assert f() == 1\n",
    )
    builder.ensure_built("a")
    test_file = (tmp_path / "build" / "test_a.py").read_text()
    compile(test_file, "test_a.py", "exec")  # must not raise SyntaxError
    assert test_file.index("from __future__") < test_file.index("AUTO-GENERATED")
