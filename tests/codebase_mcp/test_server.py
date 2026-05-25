from grimoire.codebase_mcp.config import McpConfig
from grimoire.codebase_mcp.server import build_server, TOOL_NAMES
from grimoire.codebase_mcp.workspace import Workspace
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


def test_classification_tools_registered():
    from grimoire.codebase_mcp.server import TOOL_NAMES
    assert "mark_tool" in TOOL_NAMES and "mark_helper" in TOOL_NAMES


def test_safe_tool_returns_structured_error_on_exception():
    from grimoire.codebase_mcp.server import _safe_tool

    def boom(x: int) -> dict:
        raise RuntimeError("kaboom")

    out = _safe_tool(boom)(1)
    assert out["ok"] is False
    assert out["reason"] == "internal-error"
    assert "kaboom" in out["detail"]


def test_safe_tool_preserves_signature():
    """The backstop must not change the function signature, or FastMCP would
    build the wrong input schema for the tool."""
    import inspect
    from grimoire.codebase_mcp.server import _safe_tool

    def f(a: int, b: str = "x") -> dict:
        return {}

    assert inspect.signature(_safe_tool(f)) == inspect.signature(f)


def test_move_tool_schema_accepts_list(tmp_path):
    import asyncio
    from grimoire.codebase_mcp.config import McpConfig
    from grimoire.codebase_mcp.workspace import Workspace
    from tests.api.test_search_system import FakeEmbedder
    ws = Workspace.open(McpConfig(root=tmp_path, min_tests=0, max_folder_children=0),
                        embedder=FakeEmbedder())
    app = build_server(ws)
    tools = asyncio.run(app.list_tools())
    move = next(t for t in tools if t.name == "move")
    node_ids = move.inputSchema["properties"]["node_ids"]
    # must accept an array, not be a bare string-only schema
    assert "anyOf" in node_ids, node_ids
    assert any(s.get("type") == "array" for s in node_ids["anyOf"]), node_ids
