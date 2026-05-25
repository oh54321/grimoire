from pathlib import Path

from codebase_mcp.config import McpConfig
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def _ws(tmp_path):
    cfg = McpConfig(root=tmp_path / "cb", min_tests=0, max_folder_children=0,
                    ingest_root=tmp_path / "ing")
    return Workspace.open(cfg, embedder=FakeEmbedder())


def _src(tmp_path) -> str:
    src = tmp_path / "src"
    src.mkdir()
    (src / "api.py").write_text(
        "def ping(host):\n    'Ping.'\n    return True\n"
    )
    return str(src)


def test_fetch_survey_read_discard_roundtrip(tmp_path):
    ws = _ws(tmp_path)
    f = ws.fetch_source(_src(tmp_path))
    assert f["ok"] is True
    session = f["session"]

    s = ws.survey_source(session)
    assert s["ok"] is True
    assert any(sym["qualname"] == "ping" for sym in s["symbols"])

    r = ws.read_source(session, "api.py", symbol="ping")
    assert "def ping(host):" in r["code"]

    d = ws.discard_source(session)
    assert d["ok"] is True


def test_fetch_bad_source_returns_error(tmp_path):
    ws = _ws(tmp_path)
    out = ws.fetch_source(str(tmp_path / "missing"))
    assert out["ok"] is False
    assert out["reason"] == "fetch-failed"


def test_read_source_whole_file(tmp_path):
    ws = _ws(tmp_path)
    f = ws.fetch_source(_src(tmp_path))
    r = ws.read_source(f["session"], "api.py")   # no symbol -> whole file
    assert "def ping(host):" in r["code"]


def test_survey_source_with_path_subdir(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "top.py").write_text("def top():\n    return 0\n")
    (src / "pkg" / "inner.py").write_text("def inner():\n    return 1\n")
    ws = _ws(tmp_path)
    f = ws.fetch_source(str(src))
    s = ws.survey_source(f["session"], path="pkg")
    quals = {sym["qualname"] for sym in s["symbols"]}
    assert "inner" in quals
    assert "top" not in quals
