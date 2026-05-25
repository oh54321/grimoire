from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from codebase_mcp.workspace import Workspace

GUIDANCE = (
    "A personal, test-gated library of reusable code. Workflow: discover/search before "
    "writing, and reuse existing nodes as dependencies. Decompose into small single-purpose "
    "nodes; build internal helpers as separate nodes created with searchable=False and compose "
    "them as dependencies so search stays lean. implement requires passing tests (the only way "
    "code enters the library). Create folders as needed; when an op returns folder-full, make a "
    "subfolder and move related nodes into it, then retry. Use run_scratch for throwaway macros "
    "against built nodes."
)

TOOL_NAMES = [
    "discover", "search", "search_tags", "list_tags",
    "view", "read_code", "read_tests", "children", "tree",
    "define", "implement", "dirty", "rebuild",
    "make_folder", "move", "rename", "remove", "hide", "show", "health",
    "run_scratch",
]


def build_server(workspace: Workspace) -> FastMCP:
    app = FastMCP("haymanbot-codebase", instructions=GUIDANCE)
    for name in TOOL_NAMES:
        app.tool(name=name)(getattr(workspace, name))
    return app


def main() -> None:
    app = build_server(Workspace.open())
    app.run()


if __name__ == "__main__":
    main()
