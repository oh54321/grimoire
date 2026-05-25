import asyncio

from grimoire.codebase_mcp.config import McpConfig
from grimoire.codebase_mcp.server import build_server, TOOL_NAMES
from grimoire.codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def test_ingest_tools_in_tool_names():
    for name in ("fetch_source", "survey_source", "read_source", "discard_source"):
        assert name in TOOL_NAMES
        assert hasattr(Workspace, name)


def test_ingest_tools_registered_on_server(tmp_path):
    ws = Workspace.open(McpConfig(root=tmp_path / "cb", min_tests=0,
                                  max_folder_children=0, ingest_root=tmp_path / "ing"),
                        embedder=FakeEmbedder())
    app = build_server(ws)
    tools = asyncio.run(app.list_tools())
    names = {t.name for t in tools}
    assert {"fetch_source", "survey_source", "read_source", "discard_source"} <= names
