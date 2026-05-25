import pytest
from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path, **overrides):
    return Codebase.open(tmp_path, embedder=FakeEmbedder(), **overrides)


def test_define_searchable_false_hidden_by_default(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("secret", "hidden helper", searchable=False)
    assert nid not in {h.node_id for h in cb.search("secret").hits}
    assert nid in {h.node_id for h in cb.search("secret", include_hidden=True).hits}


def test_set_searchable_toggles_visibility(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("widget", "a widget")
    assert nid in {h.node_id for h in cb.search("widget").hits}
    cb.set_searchable(nid, False)
    assert nid not in {h.node_id for h in cb.search("widget").hits}
    cb.set_searchable(nid, True)
    assert nid in {h.node_id for h in cb.search("widget").hits}


def test_tag_filter_is_or(tmp_path):
    cb = _open(tmp_path)
    a = cb.add_method("aa", "alpha", tags=["x"])
    b = cb.add_method("bb", "beta", tags=["y"])
    ids = {h.node_id for h in cb.search("thing", tags=["x", "y"]).hits}
    assert a in ids and b in ids          # match >=1 tag (OR), not AND


def test_hidden_node_usable_as_dependency(tmp_path):
    cb = _open(tmp_path)
    helper = cb.add_method("helper", "internal helper", searchable=False)
    cb.implement(helper, "def helper():\n    return 7\n",
                 "def test_h():\n    assert helper() == 7\n")
    caller = cb.add_method("caller", "uses helper", dependencies=[helper])
    cb.implement(caller, "def caller():\n    return helper() + 1\n",
                 "def test_c():\n    assert caller() == 8\n")
    assert helper not in {h.node_id for h in cb.search("helper").hits}
    assert helper in {h.node_id for h in cb.search("helper", include_hidden=True).hits}
    assert caller in {h.node_id for h in cb.search("caller").hits}
