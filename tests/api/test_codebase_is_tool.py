import pytest
from grimoire.api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path, **o):
    return Codebase.open(tmp_path, embedder=FakeEmbedder(), **o)


def test_search_filters_by_is_tool(tmp_path):
    cb = _open(tmp_path)
    tool = cb.add_method("runit", "a runnable tool", is_tool=True)
    helper = cb.add_method("helpit", "a helper", is_tool=False)
    tools = {h.node_id for h in cb.search("thing", is_tool=True).hits}
    helpers = {h.node_id for h in cb.search("thing", is_tool=False).hits}
    both = {h.node_id for h in cb.search("thing").hits}
    assert tool in tools and helper not in tools
    assert helper in helpers and tool not in helpers
    assert {tool, helper} <= both


def test_default_is_tool_true(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("x", "y")
    assert cb.load(nid).is_tool is True


def test_set_is_tool_toggles_filter(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("widget", "a widget")
    assert nid in {h.node_id for h in cb.search("widget", is_tool=True).hits}
    cb.set_is_tool(nid, False)
    assert nid not in {h.node_id for h in cb.search("widget", is_tool=True).hits}
    assert nid in {h.node_id for h in cb.search("widget", is_tool=False).hits}
