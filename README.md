# 🪄 Grimoire

**An MCP server that gives Claude a persistent, test-gated, semantically-searchable library of reusable code — a grimoire of "spells" it grows, composes, and casts.**

Instead of re-reading a whole codebase to recall what exists, Claude searches Grimoire for a capability, reuses it as a dependency, or writes a new one that only enters the library once its tests pass. The library lives on disk and persists across sessions.

> Built on the [Model Context Protocol](https://modelcontextprotocol.io). Python 3.11+.

## Why

LLM coding agents keep re-deriving the same helpers and re-reading large codebases. Grimoire gives Claude a durable place to keep code worth reusing, reachable by meaning rather than file path:

- **Search before writing** — semantic search over each node's description + tags, so Claude reuses instead of duplicating.
- **Tests are the gate** — code enters only through `implement`, which builds it in isolation and requires its tests to pass.
- **Composable** — nodes declare dependencies on other nodes; cross-node imports are generated automatically.
- **Lean by design** — hide internal helpers from search (`searchable=False`) and classify tools vs helpers (`is_tool`) to keep the surface curated.
- **Persistent** — stored on disk under a configurable root; reloads across sessions.

## How it works

Grimoire is a thin MCP layer over a node-graph code store:

- **Nodes** are folders or code (method / class / executable). Each code node has a description, dependencies, tests, a `searchable` flag, and an `is_tool` flag.
- **The builder** materializes each code node to a Python module, generating `from build.<dep> import <name>` for its dependencies — authors never write cross-node imports.
- **The runner** runs tests in an isolated, warm pytest worker. `implement` trial-builds + tests a candidate and commits it only if every test passes.
- **Search** is a vector index over descriptions and composite tags (`@kind:`, `@in:`, `@searchable:`, `@tool:`); tag/folder/type filters are OR (match-any), with hidden nodes gated out by default.

The on-disk node store is the single source of truth; the build cache and search index are regenerable.

## Install

```bash
git clone https://github.com/oh54321/grimoire.git
cd grimoire
pip install .            # or: pip install -e '.[test]' for development
```

Python 3.11+. (First run downloads a sentence-transformers embedding model.)

## Run

```bash
grimoire                 # stdio MCP server (installed console script)
# or, without installing the script:
python -m codebase_mcp
```

## Use with Claude Code

```bash
claude mcp add grimoire -- grimoire
```

Or add it to an MCP client config manually:

```json
{
  "mcpServers": {
    "grimoire": {
      "command": "grimoire",
      "env": { "GRIMOIRE_CODEBASE": "~/.grimoire/codebase" }
    }
  }
}
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GRIMOIRE_CODEBASE` | `~/.grimoire/codebase` | Library root (persists across sessions) |
| `GRIMOIRE_MIN_TESTS` | `3` | Minimum passing tests `implement` requires |
| `GRIMOIRE_MAX_FOLDER_CHILDREN` | `7` | Hard cap per folder; the (N+1)th child is rejected `folder-full` |
| `GRIMOIRE_SCRATCH_TIMEOUT` | `30` | Seconds before a `run_scratch` run is killed |

## Tools

**Find & reuse**
- `discover(query)` — plain hits + candidate tags/folders + present types, for model-driven refinement
- `search(query, tags?, folders?, object_types?, include_hidden?, is_tool?)` — filters are OR/match-any; hidden nodes excluded unless `include_hidden`; `is_tool=true/false` restricts to tools/helpers
- `search_tags(query)`, `list_tags()`

**Read (stub-first)**
- `view(node_id)` — description, signature, dependencies, tests, `searchable`, `is_tool` (not the full body)
- `read_code(node_id)`, `read_tests(node_id)`, `children(folder_id?)`, `tree(folder_id?)`

**Create & test**
- `define(kind, name, description, parent?, dependencies?, tags?, searchable?, is_tool?)` — create a stub
- `implement(node_id, code, tests)` — build + test; commits only if ≥ `min_tests` pass (the only way code enters)
- `dirty()`, `rebuild(node_id?)`

**Organize & classify**
- `make_folder`, `move` (one or many), `rename`, `remove`
- `hide` / `show` (search visibility), `mark_tool` / `mark_helper` (callable tool vs helper)
- `health()` — folders at/over the cap

**Scratch**
- `run_scratch(code, deps?)` — ephemeral; imports built nodes; never persisted

## Workflow

`discover`/`search` → if nothing fits, `define` a node (reusing hits as `dependencies`) → `implement` with tests → organize into folders. Keep it lean: decompose into small nodes, hide narrow helpers (`searchable=False`), and mark them helpers (`is_tool=False`). Prototype throwaway macros against built nodes with `run_scratch`.

## Development

```bash
pip install -e '.[test]'
pytest -q
```

Layout: `src/codebase_mcp/` (MCP layer: `Workspace` core + thin FastMCP `server.py`) over `src/api/` (`Codebase` facade), `src/library/` (node store, builder, runner), and `src/search/` (vector index).

## Roadmap

- Persist the vector index (currently re-embedded from the store on each open).
- **Codebase ingestion** — pull source from an external repo into the library as test-gated nodes (`integrate-mcp` = that, pointed at an MCP server's repo).

## License

MIT © Oliver Hayman
