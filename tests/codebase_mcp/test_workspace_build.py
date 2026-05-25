import pytest
from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    base = {"min_tests": 0, "max_folder_children": 0, "scratch_timeout": 10.0}
    base.update(overrides)
    return Workspace.open(McpConfig(root=tmp_path, **base), embedder=FakeEmbedder())


def test_define_then_implement_success(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "inc", "add one")
    assert d["ok"] is True
    res = ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res["ok"] is True
    assert res["tests"][0]["status"] == "passing"
    assert d["id"] not in [n["id"] for n in ws.dirty()["nodes"]]


def test_define_rejects_bad_kind(tmp_path):
    ws = _ws(tmp_path)
    out = ws.define("widget", "x", "y")
    assert out["ok"] is False and out["reason"] == "bad-kind"


def test_define_searchable_false_hidden(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "helper", "internal", searchable=False)
    assert d["ok"] is True
    assert d["id"] not in {h["id"] for h in ws.search("internal")["hits"]}
    assert d["id"] in {h["id"] for h in ws.search("internal", include_hidden=True)["hits"]}


def test_implement_too_few_tests_structured(tmp_path):
    ws = _ws(tmp_path, min_tests=3)
    d = ws.define("method", "inc", "add one")
    res = ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                       "def test_a():\n    assert inc(1) == 2\n")
    assert res["ok"] is False and res["reason"] == "tests-failed"
    assert res["required_tests"] == 3


def test_implement_failure_lists_failing_tests(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "bad", "broken")
    res = ws.implement(d["id"], "def bad():\n    return 1\n",
                       "def test_a():\n    assert bad() == 2\n")
    assert res["ok"] is False and res["failures"][0]["name"] == "a"


def test_dirty_and_rebuild(tmp_path):
    ws = _ws(tmp_path)
    d = ws.define("method", "inc", "add one")
    assert d["id"] in [n["id"] for n in ws.dirty()["nodes"]]
    ws.implement(d["id"], "def inc(x):\n    return x + 1\n",
                 "def test_a():\n    assert inc(1) == 2\n")
    report = ws.rebuild()
    assert "rebuilt" in report and "failed" in report
