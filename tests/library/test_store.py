import numpy as np
import pytest
from pathlib import Path

from library.config import LibraryConfig
from library.errors import (
    CorruptMetaFile,
    DescriptionTooLong,
    InvalidNodeName,
    NodeNotFound,
)
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


def test_load_missing_id_raises_node_not_found(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    with pytest.raises(NodeNotFound):
        store.load("never_existed")


def test_load_corrupt_json_raises(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    d = tmp_path / "abc"
    d.mkdir()
    (d / "meta.json").write_text("{ not valid json")
    with pytest.raises(CorruptMetaFile):
        store.load("abc")


def test_description_too_long_raises_before_any_file_written(tmp_path: Path):
    cfg = LibraryConfig(root_path=tmp_path, max_description_tokens=2)
    store = NodeStore(cfg)
    node = FolderNode(node_id="abc", name="x", description="this description has many many tokens")
    with pytest.raises(DescriptionTooLong):
        store.save(node)
    assert not (tmp_path / "abc").exists()


def test_invalid_code_node_name_raises(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    node = CodeNode(node_id="abc", name="1bad-identifier", description="x")
    with pytest.raises(InvalidNodeName):
        store.save(node, code="x = 1\n")
    assert not (tmp_path / "abc").exists()


def test_folder_node_name_can_be_arbitrary(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    folder = FolderNode(node_id="abc", name="Statistics & Helpers", description="x")
    store.save(folder)  # must not raise


def test_iter_ids_yields_all_saved_nodes(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    store.save(FolderNode(node_id="a", name="x", description="x"))
    store.save(FolderNode(node_id="b", name="x", description="x"))
    store.save(FolderNode(node_id="c", name="x", description="x"))
    assert set(store.iter_ids()) == {"a", "b", "c"}


def test_iter_ids_skips_non_node_entries(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    store.save(FolderNode(node_id="a", name="x", description="x"))
    (tmp_path / "build").mkdir()  # build dir is not a node
    (tmp_path / "config.json").write_text("{}")  # config file is not a node
    (tmp_path / "loose_dir").mkdir()  # dir with no meta.json is not a node
    assert set(store.iter_ids()) == {"a"}


def test_delete_removes_dir(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    store.save(FolderNode(node_id="a", name="x", description="x"))
    store.delete("a")
    assert not (tmp_path / "a").exists()
    assert not store.exists("a")


def test_delete_missing_raises(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    with pytest.raises(NodeNotFound):
        store.delete("never")


def test_atomic_write_no_stray_tmp_files_after_save(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    store.save(FolderNode(node_id="a", name="x", description="x"))
    files = list((tmp_path / "a").iterdir())
    assert all(not f.name.endswith(".tmp") for f in files)


def test_size_on_disk_is_positive(tmp_path: Path):
    store = NodeStore(_config(tmp_path))
    node = CodeNode(node_id="a", name="f", description="x")
    store.save(node, code="def f(): pass\n")
    assert store.size_on_disk("a") > 0


def test_searchable_roundtrips(tmp_path):
    from library.config import LibraryConfig
    from library.store import NodeStore
    from library.nodes import CodeNode
    store = NodeStore(LibraryConfig(root_path=tmp_path))
    store.save(CodeNode(node_id="c1", name="foo", description="d", searchable=False))
    assert store.load("c1").searchable is False


def test_missing_searchable_key_defaults_true(tmp_path):
    import json
    from library.config import LibraryConfig
    from library.store import NodeStore
    from library.nodes import CodeNode
    store = NodeStore(LibraryConfig(root_path=tmp_path))
    store.save(CodeNode(node_id="c2", name="bar", description="d"))
    meta = tmp_path / "c2" / "meta.json"
    data = json.loads(meta.read_text())
    data.pop("searchable", None)
    meta.write_text(json.dumps(data))
    assert store.load("c2").searchable is True


def test_is_tool_roundtrips_and_defaults_true(tmp_path):
    import json
    from library.config import LibraryConfig
    from library.store import NodeStore
    from library.nodes import CodeNode
    store = NodeStore(LibraryConfig(root_path=tmp_path))
    store.save(CodeNode(node_id="t1", name="foo", description="d", is_tool=False))
    assert store.load("t1").is_tool is False
    store.save(CodeNode(node_id="t2", name="bar", description="d"))
    meta = tmp_path / "t2" / "meta.json"
    data = json.loads(meta.read_text())
    data.pop("is_tool", None)
    meta.write_text(json.dumps(data))
    assert store.load("t2").is_tool is True
