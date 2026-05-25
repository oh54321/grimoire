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
    assert {h.node_id for h in cb.search("leaf", folders={a}).hits} == {leaf}
    cb.move(a, b)
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
