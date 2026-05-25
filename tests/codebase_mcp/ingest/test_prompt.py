import asyncio

from codebase_mcp.config import McpConfig
from codebase_mcp.ingest.prompt import build_ingest_prompt
from codebase_mcp.server import build_server
from codebase_mcp.workspace import Workspace
from tests.api.test_search_system import FakeEmbedder


def test_prompt_text_covers_workflow():
    text = build_ingest_prompt("https://example/repo.git", kind="mcp")
    for needle in ("fetch_source", "survey_source", "read_source",
                   "define", "implement", "discard_source",
                   "dependencies", "searchable"):
        assert needle in text
    assert "https://example/repo.git" in text
    assert "is_tool=True" in text          # mcp default surfaced


def test_codebase_kind_biases_helpers():
    text = build_ingest_prompt("/some/path", kind="codebase")
    assert "is_tool=False" in text


def test_prompt_registered_on_server(tmp_path):
    ws = Workspace.open(McpConfig(root=tmp_path / "cb", min_tests=0,
                                  max_folder_children=0, ingest_root=tmp_path / "ing"),
                        embedder=FakeEmbedder())
    app = build_server(ws)
    prompts = asyncio.run(app.list_prompts())
    assert any(p.name == "ingest" for p in prompts)
