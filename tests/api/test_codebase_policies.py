import pytest
from api.codebase import Codebase
from api.errors import ImplementationFailed, InvalidMove
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path, **overrides):
    return Codebase.open(tmp_path, embedder=FakeEmbedder(), **overrides)


def test_implement_rejects_too_few_tests(tmp_path):
    cb = _open(tmp_path, min_tests_per_method=3)
    nid = cb.add_method("inc", "add one")
    with pytest.raises(ImplementationFailed) as ei:
        cb.implement(nid, "def inc(x):\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    assert "need >= 3" in str(ei.value)
    assert cb.load_code(nid) == ""


def test_implement_accepts_enough_tests(tmp_path):
    cb = _open(tmp_path, min_tests_per_method=2)
    nid = cb.add_method("inc", "add one")
    tests = ("def test_a():\n    assert inc(1) == 2\n"
             "def test_b():\n    assert inc(2) == 3\n")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n", tests)
    assert res.all_passing


def test_implement_default_off_allows_single_test(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res.all_passing


def test_make_folder_blocks_over_cap(tmp_path):
    cb = _open(tmp_path, max_folder_children=2)
    cb.make_folder("a")
    cb.make_folder("b")
    with pytest.raises(InvalidMove) as ei:
        cb.make_folder("c")
    assert ei.value.reason == "folder-full"


def test_define_blocks_over_cap(tmp_path):
    cb = _open(tmp_path, max_folder_children=1)
    cb.add_method("one", "first")
    with pytest.raises(InvalidMove) as ei:
        cb.add_method("two", "second")
    assert ei.value.reason == "folder-full"


def test_cap_off_allows_many_children(tmp_path):
    cb = _open(tmp_path)
    for i in range(5):
        cb.make_folder(f"f{i}")
    assert len(cb.children_of(cb.root_id)) == 5


def test_move_many_into_folder(tmp_path):
    cb = _open(tmp_path)
    dest = cb.make_folder("dest")
    a = cb.make_folder("a")
    b = cb.make_folder("b")
    cb.move([a, b], dest)
    assert cb.children_of(dest) == {a, b}


def test_batch_move_over_cap_is_all_or_nothing(tmp_path):
    cb = _open(tmp_path, max_folder_children=3)
    dest = cb.make_folder("dest")          # root: 1 child
    s1 = cb.make_folder("s1")              # root: 2
    s2 = cb.make_folder("s2")              # root: 3 (at cap, allowed)
    a = cb.add_method("a", "x", parent_id=s1)
    b = cb.add_method("b", "x", parent_id=s1)   # s1: 2
    c = cb.add_method("c", "x", parent_id=s2)
    d = cb.add_method("d", "x", parent_id=s2)   # s2: 2
    with pytest.raises(InvalidMove) as ei:
        cb.move([a, b, c, d], dest)        # dest would hold 4 > 3
    assert ei.value.reason == "folder-full"
    assert cb.children_of(dest) == set()   # nothing moved


def test_single_move_still_works(tmp_path):
    cb = _open(tmp_path)
    a = cb.make_folder("a")
    b = cb.make_folder("b")
    cb.move(a, b)
    assert a in cb.children_of(b)
