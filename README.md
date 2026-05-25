<div align="center">

# 🪄 Grimoire

### A spellbook of reusable code for Claude

**A persistent, test-gated, semantically-searchable library that Claude grows, composes, and casts — served over the [Model Context Protocol](https://modelcontextprotocol.io).**

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-server-7C3AED)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22C55E)](LICENSE)
![Tests](https://img.shields.io/badge/tests-232%20passing-brightgreen)

</div>

---

Instead of re-reading an entire codebase to recall what exists, Claude **searches Grimoire by meaning**, reuses a capability as a dependency, or writes a new one — and new code enters the library **only once its tests pass**. Everything lives on disk and persists across sessions.

## ✨ Highlights

- 🔍 **Search before writing** — semantic search over every node's description + tags, so Claude reuses instead of duplicating.
- ✅ **Tests are the gate** — code enters only through `implement`, which builds it in isolation and requires its tests to pass.
- 🧩 **Composable** — nodes declare dependencies on other nodes; cross-node imports are generated automatically.
- 🪶 **Lean by design** — hide internal helpers from search (`searchable=False`) and classify callable **tools** vs **helpers** (`is_tool`).
- 💾 **Persistent** — stored on disk under a configurable root; reloads across sessions.
- ⚡ **Scratch execution** — prototype throwaway macros against built code without polluting the library.

## 🚀 Quick start

Install the `grimoire` command in one line with [pipx](https://pipx.pypa.io), then register it with Claude Code:

```bash
pipx install git+https://github.com/oh54321/grimoire.git
claude mcp add grimoire -- grimoire
```

<details>
<summary>Other ways to install</summary>

**Clone + script** — auto-detects pipx / uv / venv:
```bash
git clone https://github.com/oh54321/grimoire.git
cd grimoire && ./install.sh
```

**Zero-install with [uv](https://docs.astral.sh/uv/)** — run straight from git (use this as the MCP command):
```bash
uvx --from git+https://github.com/oh54321/grimoire.git grimoire
```

**Plain pip**, in a virtualenv:
```bash
git clone https://github.com/oh54321/grimoire.git
cd grimoire && pip install .
```
</details>

<details>
<summary>Manual MCP client config</summary>

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
</details>

Python 3.11+. The first run downloads a sentence-transformers embedding model.

## 🧭 How it works

Grimoire is a thin MCP layer over a node-graph code store. Each **code node** (method / class / executable) carries a description, dependencies, tests, a `searchable` flag, and an `is_tool` flag. The **builder** materializes each node to a module and generates `from build.<dep> import <name>` for its dependencies — authors never write cross-node imports. The **runner** executes tests in an isolated, warm pytest worker; `implement` trial-builds a candidate and commits it **only if every test passes**.

```mermaid
flowchart TD
    Claude["🤖 Claude"] -- "MCP tools" --> Server["FastMCP server"]
    Server --> WS["Workspace (core)"]
    WS --> CB["Codebase facade"]
    WS --> Scratch["ScratchRunner · ephemeral"]
    CB --> Graph["node store · builder · pytest runner"]
    CB --> Search["vector search index"]
    Graph --> Disk[("~/.grimoire/codebase")]
```

The on-disk node store is the single source of truth; the build cache and search index are regenerable. Search is a vector index over descriptions + composite tags (`@kind:`, `@in:`, `@searchable:`, `@tool:`); tag / folder / type filters are **OR** (match-any), with hidden nodes gated out by default.

## ⚙️ Configuration

All via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `GRIMOIRE_CODEBASE` | `~/.grimoire/codebase` | Library root (persists across sessions) |
| `GRIMOIRE_MIN_TESTS` | `3` | Minimum passing tests `implement` requires |
| `GRIMOIRE_MAX_FOLDER_CHILDREN` | `7` | Hard cap per folder; the (N+1)th child is rejected `folder-full` |
| `GRIMOIRE_SCRATCH_TIMEOUT` | `30` | Seconds before a `run_scratch` run is killed |

## 🛠️ Tools

| Group | Tools |
|---|---|
| **Find & reuse** | `discover` · `search` (OR filters · `include_hidden` · `is_tool`) · `search_tags` · `list_tags` |
| **Read** (stub-first) | `view` (signature + meta, not the body) · `read_code` · `read_tests` · `children` · `tree` |
| **Create & test** | `define` · `implement` *(the gate — code enters only here)* · `dirty` · `rebuild` |
| **Organize & classify** | `make_folder` · `move` (one or many) · `rename` · `remove` · `hide`/`show` · `mark_tool`/`mark_helper` · `health` |
| **Scratch** | `run_scratch(code, deps?)` — ephemeral; imports built nodes; never persisted |

## 🔄 Workflow

```mermaid
flowchart LR
    A["discover / search"] --> B{"found it?"}
    B -- yes --> C["reuse as dependency"]
    B -- no --> D["define"]
    C --> D
    D --> E["implement + tests"]
    E -- "pass" --> F["✅ committed to library"]
    E -- "fail" --> D
```

Keep it lean: decompose into small nodes, hide narrow helpers (`searchable=False`), and mark internal building blocks as helpers (`is_tool=False`) so the searchable tool surface stays curated.

## 🧪 Development

```bash
pip install -e '.[test]'
pytest -q
```

**Layout** — `src/codebase_mcp/` (MCP layer: `Workspace` core + thin FastMCP `server.py`) over `src/api/` (`Codebase` facade), `src/library/` (node store · builder · runner), and `src/search/` (vector index).

## 🗺️ Roadmap

- Persist the vector index (currently re-embedded from the store on each open).
- **Codebase ingestion** — pull source from an external repo into the library as test-gated nodes (`integrate-mcp` = that, pointed at an MCP server's repo).

## 📄 License

MIT © Oliver Hayman
