from grimoire.codebase_mcp.config import McpConfig
from grimoire.codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    base = {"min_tests": 0, "max_folder_children": 0, "scratch_timeout": 10.0}
    base.update(overrides)
    return Workspace.open(McpConfig(root=tmp_path, **base), embedder=FakeEmbedder())


def test_make_folder_and_move_many(tmp_path):
    ws = _ws(tmp_path)
    dest = ws.make_folder("dest")["id"]
    a = ws.define("method", "a", "x")["id"]
    b = ws.define("method", "b", "x")["id"]
    out = ws.move([a, b], dest)
    assert out["ok"] is True
    assert {k["id"] for k in ws.children(dest)} == {a, b}


def test_move_into_full_folder(tmp_path):
    ws = _ws(tmp_path, max_folder_children=2)
    dest = ws.make_folder("dest")["id"]                 # root:1
    ws.define("method", "x", "x", parent=dest)          # dest:1
    ws.define("method", "y", "y", parent=dest)          # dest:2 (full)
    z = ws.make_folder("z")["id"]                       # root:2 (full, ok)
    w = ws.define("method", "w", "w", parent=z)["id"]   # z:1
    out = ws.move([w], dest)                            # dest 2->3 > 2
    assert out["ok"] is False and out["reason"] == "folder-full"
    assert out["folder_id"] == dest and out["hint"]


def test_rename_and_remove(tmp_path):
    ws = _ws(tmp_path)
    f = ws.make_folder("old")["id"]
    assert ws.rename(f, "new")["ok"] is True
    assert ws.view(f)["name"] == "new"
    assert ws.remove(f)["ok"] is True


def test_hide_and_unhide(tmp_path):
    ws = _ws(tmp_path)
    nid = ws.define("method", "widget", "a widget")["id"]
    assert nid in {h["id"] for h in ws.search("widget")["hits"]}
    assert ws.hide(nid)["searchable"] is False
    assert nid not in {h["id"] for h in ws.search("widget")["hits"]}
    assert ws.unhide(nid)["searchable"] is True
    assert nid in {h["id"] for h in ws.search("widget")["hits"]}


def test_health_lists_full_folders(tmp_path):
    ws = _ws(tmp_path, max_folder_children=2)
    f = ws.make_folder("g")["id"]
    ws.define("method", "a", "x", parent=f)
    ws.define("method", "b", "x", parent=f)
    health = ws.health()
    assert health["cap"] == 2
    assert any(o["id"] == f and o["children"] == 2 for o in health["over"])


def test_run_scratch_imports_built_node(tmp_path):
    ws = _ws(tmp_path)
    nid = ws.define("method", "inc", "add one")["id"]
    ws.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_a():\n    assert inc(1) == 2\n")
    out = ws.run_scratch("print(inc(41))", deps=[nid])
    assert out["ok"] is True and "42" in out["stdout"]
