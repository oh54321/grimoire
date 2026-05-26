from grimoire.codebase_mcp.config import McpConfig
from grimoire.codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path, **overrides):
    base = {"min_tests": 0, "max_folder_children": 0, "scratch_timeout": 10.0}
    base.update(overrides)
    cfg = McpConfig(root=tmp_path, **base)
    return Workspace.open(cfg, embedder=FakeEmbedder())


def test_search_returns_hits(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("rolling_mean", "streaming mean of a window")
    out = ws.search("mean")
    assert any(h["id"] == nid for h in out["hits"])
    assert {"id", "kind", "name", "description", "score"} <= set(out["hits"][0])


def test_search_include_hidden(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("secret", "hidden helper", searchable=False)
    assert nid not in {h["id"] for h in ws.search("secret")["hits"]}
    assert nid in {h["id"] for h in ws.search("secret", include_hidden=True)["hits"]}


def test_view_stub_hides_body_and_shows_searchable(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("inc", "add one")
    ws._cb.implement(nid, "def inc(x):\n    # secret body\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    v = ws.view(nid)
    assert v["kind"] == "method"
    assert v["name"] == "inc"
    assert v["signature"] == "def inc(x):"
    assert v["has_code"] is True
    assert v["searchable"] is True
    assert "secret body" not in str(v)
    assert v["tests"][0]["name"] == "a"


def test_view_signature_skips_leading_import(tmp_path):
    # the signature must be the def/class line for the node, not the first source
    # line (which is often an import or module-level assignment).
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("pluralize", "plural form of a word")
    ws._cb.implement(
        nid,
        "import re\n\n\ndef pluralize(word):\n    return re.sub(r'$', 's', word)\n",
        "def test_a():\n    assert pluralize('cat') == 'cats'\n",
    )
    assert ws.view(nid)["signature"] == "def pluralize(word):"


def test_read_code_and_tests(tmp_path):
    ws = _ws(tmp_path)
    nid = ws._cb.add_method("inc", "add one")
    ws._cb.implement(nid, "def inc(x):\n    return x + 1\n",
                     "def test_a():\n    assert inc(1) == 2\n")
    assert "return x + 1" in ws.read_code(nid)["code"]
    assert "def test_a" in ws.read_tests(nid)["tests"]


def test_tree_and_children(tmp_path):
    ws = _ws(tmp_path)
    f = ws._cb.make_folder("utils")
    leaf = ws._cb.add_method("inc", "add one", parent_id=f)
    kids = ws.children(f)
    assert [k["id"] for k in kids] == [leaf]
    tree = ws.tree()
    assert tree["kind"] == "folder"
    assert any(c["id"] == f for c in tree["children"])


def test_view_folder_lists_children_and_searchable(tmp_path):
    ws = _ws(tmp_path)
    f = ws._cb.make_folder("grp", description="a group")
    child = ws._cb.add_method("inc", "add one", parent_id=f)
    v = ws.view(f)
    assert v["kind"] == "folder"
    assert v["searchable"] is True
    assert child in {c["id"] for c in v["children"]}


def test_discover_gathers_candidates(tmp_path):
    ws = _ws(tmp_path)
    f = ws._cb.make_folder("mathlib")
    nid = ws._cb.add_method("adder", "adds two numbers", parent_id=f, tags=["arithmetic"])
    out = ws.discover("add numbers")
    assert set(out) >= {"hits", "candidate_tags", "candidate_folders", "object_types_present", "hint"}
    assert "arithmetic" in {t["tag"] for t in out["candidate_tags"]}
    assert f in {c["id"] for c in out["candidate_folders"]}
