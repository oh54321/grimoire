import numpy as np
import pytest

from grimoire.library.nodes import (
    Tag,
    Node,
    FolderNode,
    CodeNode,
    Test,
    TestStatus,
)


def test_tag_hash_uses_text_only():
    a = Tag(text="stats", v=np.array([0.1, 0.2]))
    b = Tag(text="stats", v=np.array([99.0, -1.0]))
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_tag_inequality_by_text():
    a = Tag(text="stats", v=np.zeros(3))
    b = Tag(text="other", v=np.zeros(3))
    assert a != b
    assert hash(a) != hash(b)


def test_tags_usable_in_set():
    t = {Tag(text="a", v=np.zeros(1)), Tag(text="b", v=np.zeros(1))}
    assert len(t) == 2


def test_test_default_status_is_unrun():
    t = Test(name="handles_empty")
    assert t.status is TestStatus.UNRUN


def test_test_status_string_values():
    assert TestStatus.UNRUN.value == "unrun"
    assert TestStatus.PASSING.value == "passing"
    assert TestStatus.FAILING.value == "failing"


def test_folder_node_default_children_empty_set():
    f = FolderNode(node_id="abc", name="utils", description="utility helpers")
    assert f.children == set()
    assert f.parent_id is None
    assert f.node_type == "folder"


def test_folder_node_node_type_classvar_not_in_fields():
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(FolderNode)}
    assert "node_type" not in field_names


def test_code_node_defaults():
    c = CodeNode(node_id="abc", name="rolling_mean", description="...")
    assert c.dependencies == set()
    assert c.object_type == "method"
    assert c.tests == []
    assert c.node_type == "code"


def test_code_node_dependency_field_independent_per_instance():
    a = CodeNode(node_id="a", name="a", description="")
    b = CodeNode(node_id="b", name="b", description="")
    a.dependencies.add("dep1")
    assert b.dependencies == set()


def test_node_equality_includes_node_id():
    a = FolderNode(node_id="1", name="x", description="x")
    b = FolderNode(node_id="2", name="x", description="x")
    assert a != b


def test_nodes_default_searchable_true():
    from grimoire.library.nodes import CodeNode, FolderNode
    assert CodeNode(node_id="c", name="foo", description="d").searchable is True
    assert FolderNode(node_id="f", name="grp", description="d").searchable is True
