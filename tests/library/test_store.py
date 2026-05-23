import numpy as np
import pytest
from pathlib import Path

from library.config import LibraryConfig
from library.nodes import CodeNode, FolderNode, Tag, Test, TestStatus
from library.store import NodeStore


def _config(tmp_path: Path) -> LibraryConfig:
    return LibraryConfig(root_path=tmp_path, max_description_tokens=10_000)


def test_save_and_load_folder_node(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    folder = FolderNode(node_id="abc", name="utils", description="bag of helpers")
    folder.children.add("child1")
    folder.tags.add(Tag(text="grouping", v=np.array([0.1, 0.2])))
    store.save(folder)

    loaded = store.load("abc")
    assert isinstance(loaded, FolderNode)
    assert loaded.node_id == "abc"
    assert loaded.name == "utils"
    assert loaded.description == "bag of helpers"
    assert loaded.children == {"child1"}
    assert {t.text for t in loaded.tags} == {"grouping"}


def test_save_and_load_code_node_with_code_and_tests(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    node = CodeNode(node_id="abc", name="rolling_mean", description="streaming mean")
    node.dependencies.add("def456")
    node.tests.append(Test(name="empty_input", status=TestStatus.PASSING))
    code = "def rolling_mean(xs, n): return sum(xs[-n:]) / n\n"
    tests = "def test_empty_input(): assert True\n"
    store.save(node, code=code, tests=tests)

    loaded = store.load("abc")
    assert isinstance(loaded, CodeNode)
    assert loaded.dependencies == {"def456"}
    assert len(loaded.tests) == 1
    assert loaded.tests[0].name == "empty_input"
    assert loaded.tests[0].status is TestStatus.PASSING
    assert store.load_code("abc") == code
    assert store.load_tests("abc") == tests


def test_load_tests_returns_empty_string_when_absent(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    node = CodeNode(node_id="abc", name="f", description="x")
    store.save(node, code="def f(): pass\n")
    assert store.load_tests("abc") == ""


def test_exists_true_after_save_false_otherwise(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    assert not store.exists("abc")
    store.save(FolderNode(node_id="abc", name="x", description="x"))
    assert store.exists("abc")
