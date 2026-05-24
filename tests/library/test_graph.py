from pathlib import Path

import numpy as np
import pytest

from library.errors import DuplicateNodeId, NodeNotFound
from library.graph import Graph
from library.nodes import CodeNode, FolderNode, Tag, Test, TestStatus


def test_open_on_empty_root_works(tmp_path: Path):
    g = Graph.open(tmp_path)
    assert list(g._store.iter_ids()) == []


def test_add_node_then_get(tmp_path: Path):
    g = Graph.open(tmp_path)
    node = FolderNode(node_id="a", name="utils", description="x")
    g.add_node(node)
    loaded = g.get("a")
    assert loaded.node_id == "a"


def test_add_node_duplicate_raises(tmp_path: Path):
    g = Graph.open(tmp_path)
    g.add_node(FolderNode(node_id="a", name="x", description="x"))
    with pytest.raises(DuplicateNodeId):
        g.add_node(FolderNode(node_id="a", name="x", description="x"))


def test_index_rebuild_on_reopen(tmp_path: Path):
    g = Graph.open(tmp_path)
    parent = FolderNode(node_id="p", name="parent", description="x")
    parent.tags.add(Tag(text="t1", v=np.zeros(2)))
    child = FolderNode(node_id="c", name="child", description="x", parent_id="p")
    parent.children.add("c")
    g.add_node(parent)
    g.add_node(child)

    g2 = Graph.open(tmp_path)
    assert g2.children_of("p") == {"c"}
    assert g2.parent_of("c") == "p"
    assert g2.find_by_tag("t1") == {"p"}


def test_dependencies_and_dependents(tmp_path: Path):
    g = Graph.open(tmp_path)
    dep = CodeNode(node_id="d", name="leaf", description="x")
    parent = CodeNode(node_id="p", name="top", description="x", dependencies={"d"})
    g.add_node(dep, code="def leaf(): return 0\n")
    g.add_node(parent, code="def top(): return leaf()\n")
    assert g.dependencies_of("p") == {"d"}
    assert g.dependents_of("d") == {"p"}


def test_remove_node_cascades_through_index(tmp_path: Path):
    g = Graph.open(tmp_path)
    dep = CodeNode(node_id="d", name="leaf", description="x")
    parent = CodeNode(node_id="p", name="top", description="x", dependencies={"d"})
    g.add_node(dep, code="def leaf(): return 0\n")
    g.add_node(parent, code="def top(): return leaf()\n")

    g.remove_node("p")
    assert g.dependents_of("d") == set()
    with pytest.raises(NodeNotFound):
        g.get("p")


def test_update_node_changes_tags_in_index(tmp_path: Path):
    g = Graph.open(tmp_path)
    folder = FolderNode(node_id="a", name="x", description="x")
    folder.tags.add(Tag(text="old", v=np.zeros(1)))
    g.add_node(folder)
    assert g.find_by_tag("old") == {"a"}

    folder2 = FolderNode(node_id="a", name="x", description="x")
    folder2.tags.add(Tag(text="new", v=np.zeros(1)))
    g.update_node(folder2)
    assert g.find_by_tag("old") == set()
    assert g.find_by_tag("new") == {"a"}


def test_get_code_and_get_tests(tmp_path: Path):
    g = Graph.open(tmp_path)
    g.add_node(
        CodeNode(node_id="a", name="f", description="x"),
        code="def f(): pass\n",
        tests="def test_x(): pass\n",
    )
    assert g.get_code("a") == "def f(): pass\n"
    assert g.get_tests("a") == "def test_x(): pass\n"


def test_config_overrides_persisted_on_open(tmp_path: Path):
    g = Graph.open(tmp_path, max_cache_mb=7)
    g_again = Graph.open(tmp_path)
    assert g_again._config.max_cache_mb == 7


def test_ensure_built_via_graph(tmp_path: Path):
    g = Graph.open(tmp_path)
    g.add_node(CodeNode(node_id="a", name="f", description="x"), code="def f(): return 1\n")
    assert g.ensure_built("a") is True
    assert g.ensure_built("a") is False


def test_run_tests_end_to_end_with_dependency(tmp_path: Path):
    g = Graph.open(tmp_path)
    g.add_node(
        CodeNode(node_id="d", name="add_one", description="x"),
        code="def add_one(x): return x + 1\n",
    )
    g.add_node(
        CodeNode(
            node_id="p",
            name="add_two",
            description="x",
            dependencies={"d"},
            tests=[Test(name="basic")],
        ),
        code="def add_two(x): return add_one(x) + 1\n",
        tests="def test_basic(): assert add_two(0) == 2\n",
    )
    results = g.run_tests("p")
    assert len(results) == 1
    assert results[0].name == "basic"
    assert results[0].status is TestStatus.PASSING

    # The node's persisted test status should also be updated.
    updated = g.get("p")
    assert isinstance(updated, CodeNode)
    assert updated.tests[0].status is TestStatus.PASSING


def test_public_reexports():
    """Top-level package re-exports the public surface."""
    import library
    for name in [
        "Graph",
        "Node",
        "FolderNode",
        "CodeNode",
        "Tag",
        "Test",
        "TestStatus",
        "TestResult",
        "LibraryConfig",
        "NodeNotFound",
        "DuplicateNodeId",
        "DescriptionTooLong",
        "InvalidNodeName",
        "MissingDependency",
        "BuildError",
        "CorruptMetaFile",
        "new_node_id",
    ]:
        assert hasattr(library, name), f"missing public export: {name}"
