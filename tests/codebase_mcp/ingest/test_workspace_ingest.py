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
