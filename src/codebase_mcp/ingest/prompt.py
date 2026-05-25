from __future__ import annotations


def build_ingest_prompt(source: str, kind: str = "auto") -> str:
    """Guided workflow for ingesting a source into the library as test-gated nodes."""
    if kind == "mcp":
        bias = ("This is an MCP server: its tool/handler functions bias to "
                "is_tool=True; their internal helpers to is_tool=False, searchable=False.")
    elif kind == "codebase":
        bias = ("This is a general codebase: everything biases to helpers "
                "(is_tool=False); mark narrow internals searchable=False; promote to "
                "is_tool=True only when broadly reusable.")
    else:
        bias = ("Decide kind from fetch_source's looks_like_mcp: MCP tool functions "
                "bias to is_tool=True; general code biases to is_tool=False helpers.")
    return (
        f"Ingest `{source}` into the Grimoire library as test-gated nodes.\n\n"
        f"{bias}\n\n"
        "Workflow:\n"
        f"1. fetch_source('{source}') -> survey_source(session). Skim the symbols.\n"
        "2. For each symbol decide: tool / helper / skip.\n"
        "3. Dependencies first: ingest a symbol's callees as helper nodes BEFORE the "
        "symbol that needs them, wiring them as `dependencies`.\n"
        "4. For each kept symbol: read_source(session, module, symbol=qualname) — "
        "`module` is the file path from survey_source (e.g. pkg/api.py) and `symbol` "
        "is its qualname. First search(...) to confirm the library doesn't already "
        "have it and to find existing nodes to reuse as `dependencies`. Then "
        "define(...) with the right kind/is_tool/searchable -> write real tests -> "
        "implement(...). Fix until the pytest gate passes (the only way code enters "
        "the library).\n"
        "5. discard_source(session) when done.\n\n"
        "Restraint: ingest sparingly; do not import the cloned source — copy/adapt code "
        "into self-contained nodes that depend only on other nodes."
    )
