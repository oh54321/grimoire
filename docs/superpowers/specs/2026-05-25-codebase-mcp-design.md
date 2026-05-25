# Codebase MCP: a personal, test-gated function library for Claude — Design

**Date:** 2026-05-25
**Status:** Approved (pending user review)
**Scope:** A new `src/codebase_mcp/` package exposing the existing `src/api/` `Codebase` as an MCP server, so Claude can grow and reuse a personal library of functions, classes, and executables — searching and reading stubs before writing, implementing with a required number of tests, refactoring under a hard folder-size cap, and running ephemeral scratch scripts against the built library. Plus two small, config-gated policy additions to `src/api`/`library`.

## Goal

Give Claude a durable place to keep code it expects to reuse, reachable through a small, opinionated tool surface that matches how it actually works:

- **discover** what already exists by meaning (`search`) before writing anything;
- **read stubs** (signature + description + test names) cheaply, full source only on demand, so reuse is the default;
- **create** an abstraction and **implement** it with a required minimum number of passing tests — the only way code enters the library;
- **refactor** the tree with direct folder primitives, under a hard cap that forces a split before a folder grows past a threshold;
- **prototype** throwaway macros/scripts that import the built library but never get committed to it.

The library is for things worth reusing. Throwaway work goes through scratch and is never persisted to the graph.

## Design principle: one source of truth, rules live with the data

`src/api`'s `Codebase` already establishes that the on-disk node store is the only authoritative state; the build manifest and vector indices are regenerable caches (see `2026-05-24-codebase-api-design.md`). This spec preserves that and adds **no new authoritative state**.

The two new policies — *every method needs at least N tests* and *a folder may not exceed M children* — are **integrity rules about the codebase itself**, so they live in `Codebase`/`LibraryConfig`, not in the MCP layer. They are gated behind config and default to *off* (0), so existing behavior and tests are unchanged. The MCP server is the caller that turns them on. Consequence: the invariants hold for **every** caller (tests, a future CLI, the MCP), not only when you go through the MCP.

The MCP package adds exactly one piece of in-memory orchestration — `Workspace` — and one piece of ephemeral, never-persisted machinery — `ScratchRunner`.

## Architecture

```
server.py (FastMCP, stdio)
   │  thin tool functions: JSON args -> Workspace call -> rendered result
   ▼
Workspace  ── owns ──▶  Codebase       (src/api: graph + search, the authoritative library)
   │
   └────── owns ──▶  ScratchRunner   (src/mcp: ephemeral subprocess execution, no persistence)
```

- **`Workspace`** (`src/mcp/workspace.py`) is transport-agnostic and the only object the tools call. It opens one `Codebase` at the configured root, holds the `ScratchRunner`, renders stub/search/health views, and translates `Codebase` exceptions into structured, actionable results. **It is fully unit-testable without any MCP runtime.**
- **`ScratchRunner`** (`src/mcp/scratch.py`) writes an ephemeral file, optionally prepends `from build.<id> import <name>` import lines for requested dependency node ids (ensuring they are built first via `Codebase.ensure_built`), runs it as a subprocess with the store root on `PYTHONPATH` (reusing the `library.runner._worker_env` pattern), captures stdout/stderr/exit code, and deletes the file. Nothing it does touches the node store.
- **`server.py`** is a thin FastMCP binding: each tool is a small function mapping arguments to a `Workspace` method and returning its rendered result. `python -m codebase_mcp` runs it over stdio.

`Workspace` is the single coordination point, mirroring how `Codebase` is the single coordinator below it.

## Module layout

The package is named `codebase_mcp`, **not** `mcp`: with `pythonpath = ["src"]`, a package literally named `mcp` would shadow the official `mcp` SDK and break `from mcp.server.fastmcp import FastMCP` inside our own code. It still lives under `src/`, and `pyproject.toml`'s `packages.find` `include` list gains `codebase_mcp*`.

```
src/codebase_mcp/
├── __init__.py     # re-exports: Workspace, McpConfig
├── config.py       # McpConfig: root path + policy/scratch settings, from env with defaults
├── workspace.py    # Workspace — opens Codebase, owns ScratchRunner, renders results
├── scratch.py      # ScratchRunner — ephemeral subprocess runs that can import built nodes
└── server.py       # FastMCP server; tool functions; __main__ runs stdio
```

Run with `python -m codebase_mcp` (stdio). The `mcp` Python SDK (`mcp>=1.0`, providing `FastMCP`) is added to `pyproject.toml` dependencies.

## Configuration (`McpConfig`)

Resolved at server start, env-overridable, with defaults:

- `root` — the codebase location. Env `HAYMANBOT_CODEBASE`, default `~/.haymanbot/codebase`. Created on first open (the root `FolderNode` is created by `Codebase` as today).
- `min_tests` — required minimum tests per method. Default **3**. Passed through to `LibraryConfig.min_tests_per_method`.
- `max_folder_children` — hard cap on children per folder. Default **7**. Passed through to `LibraryConfig.max_folder_children`.
- `scratch_timeout` — seconds before a scratch run is killed. Default **30**.

`Workspace.open()` applies `min_tests`/`max_folder_children` as `Codebase.open(...)` config overrides (the existing `**config_overrides` path), so the policies are enforced inside `Codebase`.

## Small `src/api` / `library` additions (the source of truth)

All additive and gated; defaults preserve current behavior.

1. **`LibraryConfig`**: two new fields, `min_tests_per_method: int = 0` and `max_folder_children: int = 0` (0 = unlimited / no extra floor). Already serialized by the existing `asdict`/`fields` round-trip.
2. **`Codebase.implement`**: when `min_tests_per_method > 0`, count the test functions in `tests` *before* trial-running; if fewer than the floor, raise `ImplementationFailed` with a clear message (`got K tests, need >= N`) without building. The existing "all tests must pass" rule is unchanged and still applies on top.
3. **`Codebase.define_abstraction` / `make_folder` / `move`**: when `max_folder_children > 0`, reject any operation that would push a target folder's child count over the cap, raising `InvalidMove(reason="folder-full")`. For a batch `move`, the check is done up front against the full batch so the move is all-or-nothing.
4. **`Codebase.move`**: accept either a single node id or a list of node ids (move many into one parent). Single-id behavior is unchanged.
5. **`Codebase.ensure_built(node_ids)`**: thin delegate to `Graph.ensure_built`, used by `ScratchRunner` to guarantee requested deps are materialized before a scratch import. Generally useful and keeps `Graph` private to `Codebase`.

Test-counting note: tests are pytest functions; the count is the number of **top-level** `def test_*` functions parsed from the `tests` text (via `ast`), matching how the runner collects flat test files. Nested/closure `test_*` defs do not count.

## Revision (2026-05-25): searchability, OR tag matching, discovery, guidance

Four execution-time requirements, folded in here. They reshape the search surface but add no new authoritative state beyond one node field.

### R1 — Searchability (distributed code)

Goal: keep reusable helpers in the library without cluttering search. Code should be *distributed* — small pieces composed via dependencies, with internal helpers hidden from discovery but still usable.

- **Data model:** a new `searchable: bool = True` field on `Node` (so it applies to both `CodeNode` and `FolderNode`). Persisted in `meta.json` (`store._node_to_dict`/`_dict_to_node`, defaulting to `True` for older nodes).
- **Index:** `Codebase._composite_tags` adds `@searchable:true` or `@searchable:false`. Toggling visibility is therefore a cheap `update_tags` (no re-embed), consistent with `@kind:`/`@in:`.
- **A hidden node still builds, still runs tests, and is still usable as a dependency** of other nodes — it is only absent from default search results.
- **Defaults:** new nodes are searchable. Claude explicitly hides internal helpers.

### R2 — Tag/folder/type filters are OR (match ≥1), with searchable as an AND gate

`search_filtered` already supports `all_tags` (AND) + `any_groups` (OR-within-group, AND-across-groups). Today `SearchSystem` routes the user `tags` param into `all_tags` (AND) — wrong. Revised:

- user `tags` → **one OR group** (a hit matches if it has **≥1** of them);
- `folders`, `object_types` → their own OR groups (already are);
- `@searchable:true` → `all_tags` (an independent AND gate), added by `Codebase.search` unless `include_hidden=True`.

Net filter: `searchable AND (≥1 tag) AND (≥1 folder) AND (≥1 type)`. The low-level `TaggedKVDatabase.search` (used elsewhere, AND semantics) is **not** changed.

### R3 — `discover(query)`: model-driven fallback pipeline

The model — not a threshold — decides whether plain results are good enough. `discover` makes that judgment cheap by gathering, in one call:

- `hits` — the plain semantic search;
- `candidate_tags` — from `search_tags(query)`;
- `candidate_folders` — a folder-kind search (`search(query, object_types=["folder"], include_hidden=True)`);
- `object_types_present` — distinct kinds among `hits`;
- a `hint` describing the refine step.

If the plain `hits` look weak, Claude refines by calling `search(query, tags=[...], folders=[...], object_types=[...])` with filters chosen from the candidates (OR/match-any). No automatic weak-detection or magic threshold.

### R4 — Guidance to Claude (decomposition + folder self-management)

These are workflow nudges delivered through tool descriptions, structured-result hints, and the README — the architecture already supports them:

- **Decompose:** prefer small, single-purpose nodes; build internal helpers as separate nodes (hidden via `searchable=False`) and compose them as dependencies.
- **Folders:** create folders as needed; when a `move`/`define`/`make_folder` returns `folder-full`, the result carries a `hint` to create a subfolder (`make_folder`) and `move` related nodes into it (or move some children out), then retry. `health()` surfaces folders at/over the cap.

### Revised api/library additions

In addition to items 1–5 above:

6. **`Node.searchable: bool = True`** (R1) + store serialization.
7. **`Codebase` searchability:** `@searchable:` composite tag; `define_abstraction`/`make_folder` accept `searchable=True`; `set_searchable(node_id, value)` (persist + cheap retag).
8. **`SearchSystem` + `Codebase.search`:** route `tags` as an OR group; `Codebase.search(..., include_hidden=False)` adds the `@searchable:true` AND gate (R2). `SearchSystem.search_page` gains a `require_all` set for the gate; cache key includes it.

## Tool surface

Curated around the discover -> reuse -> implement -> refactor loop. Direct folder primitives are first-class (not composed away), because the user will sometimes drive structure explicitly ("make a folder and move these there").

**d. Search / reuse first**
- `discover(query)` — one-shot gather for model-driven refinement: plain hits + candidate tags + candidate folders + object types present + a hint (R3).
- `search(query, *, tags=, object_types=, folders=, page=0, include_hidden=False)` — semantic hits (id, kind, name, description, score). `tags`/`folders`/`object_types` are OR/match-any; hidden nodes excluded unless `include_hidden=True` (R2).
- `search_tags(query, page=0)` — discover relevant tags.
- `list_tags()` — full real-tag vocabulary.

**c. Read what exists (stub-first)**
- `view(node_id)` — the stub: kind, name, description, dependencies (id + name), tags, and test names + statuses. The signature/first line of code is shown if code exists, but **not the full body**. This is the "see what's here before writing" view.
- `read_code(node_id)` — full source, on demand.
- `read_tests(node_id)` — full test source, on demand.
- `tree(folder_id=None)` / `children(folder_id)` — browse structure from a folder (default root).

**a. Create + test**
- `define(kind, name, description, *, parent=, dependencies=, tags=, searchable=True)` — create a stub `CodeNode` (`kind` in class/method/executable). No code yet; dirty. Pass `searchable=False` for an internal helper that should stay out of search.
- `implement(node_id, code, tests)` — the gate. Enforces `>= min_tests`, trial-builds against deps, runs pytest in the warm worker, commits only if all pass; otherwise returns the failing test names + first line of each failure so Claude can iterate. Rolls back on failure (existing behavior).
- `status()` / `dirty()` — what currently needs rebuilding.
- `rebuild(node_id=None)` — incrementally regenerate + revalidate dirty nodes; returns the `RebuildReport`.

**b. Refactor (direct primitives)**
- `make_folder(name, *, parent=, description=, tags=)`
- `move(node_ids, new_parent)` — one or many; folder-full checked up front, all-or-nothing.
- `rename(node_id, new_name)`
- `remove(node_id)`
- `make_folder(...)` accepts `searchable=True`; `hide(node_id)` / `show(node_id)` toggle a node's (or folder's) search visibility (R1).
- `health()` — lists folders at or over `max_folder_children`, so a split can be planned. (Discovery aid; the hard block is enforced in `Codebase`, not here.)

**scratch**
- `run_scratch(code, *, deps=[node_ids])` — ephemeral. Ensures `deps` are built, prepends their imports, runs the script with a timeout, returns stdout/stderr/exit. Never persisted.

## Core data flow

1. `search` for relevant existing nodes; `view` the promising hits.
2. Reuse: name the found node as a `dependency` when you `define`; or, if nothing fits, `define` a fresh stub.
3. `implement` with `code` + at least `min_tests` tests. The api trial-builds (generating dependency imports), runs pytest isolated, and commits only on all-pass; failures come back structured.
4. Organize with `make_folder` / `move` (etc.). If a folder is full, the tool returns `folder-full` with the folder id; create a subfolder and split. `health` shows which folders are near/over the cap.
5. `run_scratch` to prototype a macro that calls already-built nodes, without committing anything to the library.

## Error handling

Tools never surface raw tracebacks at the transport. `Workspace` catches `ApiError`, `ImplementationFailed`, `InvalidMove`, and `library.BuildError`, returning structured results:

- implement failure -> `{ok: false, reason, failures: [{name, detail}], required_tests, given_tests}`;
- folder-full -> `{ok: false, reason: "folder-full", folder_id, cap}` with guidance to split;
- scratch failure -> `{ok: false, exit_code, stdout, stderr}` (a non-zero exit or timeout is a normal result, not an error);
- unexpected exceptions are caught at the server boundary and returned as a generic error result with the message.

## Testing

- **`Workspace`** — unit tests driving search/define/implement/move/health against a temp-root `Codebase` (no MCP runtime). Covers reuse-as-dependency, stub vs full-code views, batch move, folder-full handling.
- **`ScratchRunner`** — runs a script that imports a built node and asserts its output; a failing script returns non-zero cleanly; a hanging script is killed at `scratch_timeout`.
- **`src/api` policy tests** — `min_tests_per_method` rejects too-few tests before building; `max_folder_children` blocks the (M+1)th child via `define`, `make_folder`, and `move` (including all-or-nothing batch move); both default-off paths leave existing behavior unchanged.
- **Integration** — one end-to-end test: `search` (miss) -> `define` -> `implement` -> `search` (hit) -> `run_scratch` importing the new node.
- **Server** — a light smoke test that the FastMCP app registers the expected tool names; deep behavior is covered via `Workspace`.

## Future work (out of scope)

- **Codebase ingestion (its own sub-project).** Ingest an external codebase's source into the library as nodes — general repo ingestion, with `integrate-mcp` (ingesting an MCP server's source) as one case. Clone into an ephemeral/sandboxed checkout, let Claude select functions/classes, create `CodeNode`s with dependencies, and require tests so each passes the `implement` gate. Builds on this core MCP (uses `define`/`implement`). Security-sensitive — needs its own design: sandboxed clone, the existing `from build.X` forbidden-import scan, no-network test runs, and human-in-the-loop selection. Deferred until the core MCP lands; gets its own spec + plan.
- Auto-suggesting *how* to split a full folder (clustering by tag/semantics) rather than only flagging it.
- Persisting/saving the vector index (the existing `SearchSystem.save()` is still uncalled — see `codebase-api-followups`).
- A saved (named, re-runnable) scratchpad, if ephemeral runs prove insufficient.
- Exporting the library to a concrete named-file project tree.
- Resource/prompt endpoints (this spec is tools-only).

## Revision 2 (2026-05-25): callable-tool vs helper classification (R5)

Each code node records whether it is a **callable tool** (a top-level capability meant to be invoked/reused) or a **helper** (an internal building block), so search can target one or the other. Independent of `searchable` (visibility): a tool may be hidden, a helper may be visible.

- **Data model:** `CodeNode.is_tool: bool = True` (tools by default; mark helpers `is_tool=False`). Persisted in `meta.json` (CodeNode branch), defaulting True for older nodes. Not applicable to folders.
- **Index:** `Codebase._composite_tags` adds `@tool:true|false` for code nodes only. Toggling is a cheap retag (no re-embed).
- **Search filter:** `Codebase.search(..., is_tool: bool | None = None)` — when set, adds `@tool:<bool>` to the AND gate (`require_all`): `is_tool=True` returns only tools, `False` only helpers, `None` (default) both.
- **API:** `define_abstraction`/`add_method`/`add_class`/`add_executable` accept `is_tool=True`; `set_is_tool(node_id, value)` toggles + retags.
- **MCP:** `define(..., is_tool=True)`, `search(..., is_tool=None)`, `view` shows `is_tool`, and `mark_tool`/`mark_helper` toggles. Guidance: define broadly-useful callables as tools; mark internal building blocks as helpers (often also `searchable=False`).
