import pytest
from api.codebase import Codebase
from api.errors import ImplementationFailed
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
