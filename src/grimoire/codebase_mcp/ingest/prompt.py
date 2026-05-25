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
        f"Ingest `{source}` into the Grimoire library as test-gated nodes. Work through "
        "three phases IN ORDER and do NOT skip ahead to building code.\n\n"
        f"{bias}\n\n"
        "PHASE A — Survey, then CLARIFY (hard stop):\n"
        f"1. fetch_source('{source}') -> survey_source(session). Read the full symbol list.\n"
        "2. STOP and ask the user, before doing anything else: what do they want from "
        "this source — which capabilities/tools matter, what to ignore, and any naming or "
        "scope constraints? Then show your proposed tool / helper / skip split of the "
        "surveyed symbols and have them CONFIRM or correct it. Do not continue until they "
        "answer; do not assume the selection is right.\n\n"
        "PHASE B — PLAN (hard stop):\n"
        "3. Produce a concrete plan covering, for each in-scope symbol: read / "
        "keep-as-tool (is_tool=True) / keep-as-helper (is_tool=False, searchable=False) / "
        "skip / refactor (split or merge into cleaner nodes). Order it dependencies-first "
        "— ingest a symbol's callees as helper nodes BEFORE the symbol that needs them, "
        "wired as `dependencies`. STOP and get the user's approval or edits before building.\n\n"
        "PHASE C — IMPLEMENT with tests:\n"
        "4. Before defining anything, search the library AGGRESSIVELY to avoid duplicates "
        "and find nodes to reuse: start with discover(query), then RE-RUN search(query, "
        "tags=[...], folders=[...], object_types=[...], is_tool=...) using the "
        "candidate_tags and candidate_folders discover surfaces (filters are OR / "
        "match-any). Never settle for one weak unfiltered query — try several phrasings "
        "and tighten filters until you are confident whether the capability already "
        "exists. Reuse any match as a `dependency`.\n"
        "5. For each kept symbol: read_source(session, module, symbol=qualname) — "
        "`module` is the file path from survey_source (e.g. pkg/api.py) and `symbol` is "
        "its qualname. define(...) with the right kind/is_tool/searchable -> write real "
        "tests -> implement(...). Fix until the pytest gate passes (the only way code "
        "enters the library). Apply any refactors from the approved plan.\n"
        "6. discard_source(session) when done.\n\n"
        "Restraint: ingest sparingly; do not import the cloned source — copy/adapt code "
        "into self-contained nodes that depend only on other nodes."
    )
