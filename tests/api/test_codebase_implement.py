import pytest
from api.codebase import Codebase
from api.errors import ImplementationFailed
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_implement_commits_on_pass(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    res = cb.implement(nid, "def inc(x):\n    return x + 1\n",
                       "def test_inc():\n    assert inc(1) == 2\n")
    assert res.all_passing
    assert cb.load_code(nid) == "def inc(x):\n    return x + 1\n"
    assert nid not in cb.dirty()


def test_implement_failure_leaves_node_untouched(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("bad", "broken")
    with pytest.raises(ImplementationFailed) as ei:
        cb.implement(nid, "def bad():\n    return 1\n",
                     "def test_bad():\n    assert bad() == 2\n")
    assert ei.value.results                      # carries per-test results
    assert cb.load_code(nid) == ""               # never wrote unvalidated code
    assert nid in cb.dirty()


def test_reimplement_preserves_prior_until_green(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_inc():\n    assert inc(1) == 2\n")
    with pytest.raises(ImplementationFailed):
        cb.implement(nid, "def inc(x):\n    return x + 5\n",
                     "def test_inc():\n    assert inc(1) == 2\n")
    assert cb.load_code(nid) == "def inc(x):\n    return x + 1\n"   # prior intact
