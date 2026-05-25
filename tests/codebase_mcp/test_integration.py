from grimoire.codebase_mcp.config import McpConfig
from grimoire.codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path):
    cfg = McpConfig(root=tmp_path, min_tests=2, max_folder_children=7, scratch_timeout=10.0)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_end_to_end_with_hidden_helper_and_discover(tmp_path):
    ws = _ws(tmp_path)
    h = ws.define("method", "helper", "internal multiply helper", searchable=False)
    assert h["ok"]
    rh = ws.implement(h["id"], "def helper(x):\n    return x * 2\n",
                      "def test_a():\n    assert helper(2) == 4\n"
                      "def test_b():\n    assert helper(0) == 0\n")
    assert rh["ok"]
    d = ws.define("method", "double_plus", "double then add one", dependencies=[h["id"]])
    rd = ws.implement(d["id"], "def double_plus(x):\n    return helper(x) + 1\n",
                      "def test_a():\n    assert double_plus(3) == 7\n"
                      "def test_b():\n    assert double_plus(0) == 1\n")
    assert rd["ok"]
    assert h["id"] not in {x["id"] for x in ws.search("multiply helper")["hits"]}
    assert d["id"] in {x["id"] for x in ws.search("double then add")["hits"]}
    disc = ws.discover("double")
    assert set(disc) >= {"hits", "candidate_tags", "candidate_folders", "object_types_present", "hint"}
    out = ws.run_scratch("print(double_plus(10))", deps=[d["id"]])
    assert out["ok"] and "21" in out["stdout"]
