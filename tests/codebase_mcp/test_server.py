from codebase_mcp.config import McpConfig
from codebase_mcp.server import build_server, TOOL_NAMES
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def test_every_tool_name_maps_to_a_workspace_method():
    for name in TOOL_NAMES:
        assert hasattr(Workspace, name), f"Workspace missing {name}"


def test_expected_tools_present():
    for name in ("discover", "search", "view", "define", "implement",
                 "make_folder", "move", "hide", "show", "health", "run_scratch"):
        assert name in TOOL_NAMES


def test_build_server_registers_without_error(tmp_path):
    cfg = McpConfig(root=tmp_path, min_tests=0, max_folder_children=0)
    ws = Workspace.open(cfg, embedder=FakeEmbedder())
    app = build_server(ws)
    assert app is not None
