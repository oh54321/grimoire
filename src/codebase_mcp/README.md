# codebase_mcp

MCP server exposing `api.Codebase` as a personal, test-gated function library Claude can search, grow, and reuse.

## Run

```bash
python -m codebase_mcp        # stdio transport
```

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `HAYMANBOT_CODEBASE` | `~/.haymanbot/codebase` | library root |
| `HAYMANBOT_MIN_TESTS` | `3` | minimum passing tests required by `implement` |
| `HAYMANBOT_MAX_FOLDER_CHILDREN` | `7` | hard cap; the (N+1)th child is rejected as `folder-full` |
| `HAYMANBOT_SCRATCH_TIMEOUT` | `30` | seconds before a `run_scratch` run is killed |

## Claude Code registration

```bash
claude mcp add codebase -- python -m codebase_mcp
```

## Tools

- **Find/reuse:** `discover` (plain hits + candidate tags/folders for model-driven refinement), `search` (tag/folder/type filters are OR/match-any; `include_hidden` to see hidden nodes), `search_tags`, `list_tags`
- **Read (stub-first):** `view` (description, signature, deps, tests, searchable — not the full body), `read_code`, `read_tests`, `children`, `tree`
- **Create/test:** `define` (`searchable=False` for internal helpers), `implement` (requires >= min_tests passing; only path code enters the library), `dirty`, `rebuild`
- **Refactor:** `make_folder`, `move` (one or many; `folder-full` returns a hint to split), `rename`, `remove`, `hide`/`show` (toggle search visibility), `health` (folders at/over the cap)
- **Scratch:** `run_scratch(code, deps=[ids])` — ephemeral; imports built nodes; never persisted

## Workflow

`discover`/`search` → judge results → reuse a hit as a dependency or `define` a new node → `implement` with tests → organize with folders. Keep the library lean: decompose into small nodes, hide narrow helpers (`searchable=False`) so they stay reusable as dependencies without cluttering search. Prototype throwaway macros with `run_scratch`.
