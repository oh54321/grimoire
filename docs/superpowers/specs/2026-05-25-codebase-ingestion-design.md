# Codebase Ingestion — Design

**Date:** 2026-05-25
**Status:** Draft (pending user review)
**Depends on:** the core Codebase MCP (`docs/superpowers/specs/2026-05-25-codebase-mcp-design.md`) — its `Workspace`/`Codebase`, `define`/`implement`, and `search`. This is a **separate sub-project**, built on top once the core MCP lands.

## Goal

Let Claude grow the personal library from existing source: point it at an external codebase (a GitHub URL — an MCP server's repo being one case, hence "integrate-mcp"), browse it, and pull selected functions/classes in as **test-gated nodes**. Ingested code is not trusted on faith: it enters the library only through the same `implement` gate as hand-written code — built in isolation and required to pass tests.

`integrate-mcp` is just ingestion pointed at an MCP server's repo; there is no separate mechanism for it.

## Design principle: ingestion is curation, not bulk import

We do **not** auto-parse a whole repo into nodes. That would mean trusting and executing arbitrary remote code wholesale, and guessing dependency graphs. Instead:

- **Fetch** the repo into an ephemeral, sandboxed checkout (never added to the node store).
- **Browse** it read-only (no execution).
- **Ingest one symbol at a time** through the existing `define` + `implement` pipeline: extract a top-level function/class's exact source, supply (or write) tests, and let `implement` build + test it in the sandboxed pytest worker. Only all-pass commits it.

The only code that ever runs is the symbol Claude explicitly chose, through the existing isolation (the warm pytest worker) and the existing `from build.X` forbidden-import scan. Claude (with the user watching) is the human-in-the-loop curator.

### Restraint: keep the library lean

Ingestion must not bloat the library. Two levers, both model-driven (Claude judges "is this broadly reusable?"):

- **Ingest sparingly.** Pull in only symbols likely to be reused. Don't mirror a whole module; prefer the few genuinely useful pieces. The existing `max_folder_children` cap and `health()` apply pressure on structure.
- **Hide the narrow ones.** A symbol that must be ingested only because it's a dependency of something useful — but isn't itself broadly reusable — is ingested with `searchable=False`. It still builds and composes as a dependency, but stays out of default search. The default search gate (`@searchable:true`) keeps these out of the way.

This is the same searchability model as the core MCP (R1), applied as ingestion policy: **default-visible for the few broadly-useful symbols, hidden for narrow helpers.** Guidance is delivered through the `ingest_symbol` tool description and the server instructions.

## Architecture

```
server.py tools  ─▶  Workspace (core)            ── owns ──▶ Codebase  (define/implement/search)
                          │
                          └── owns ──▶ RepoStore (ingest.py)   # ephemeral clones, read-only browse
```

- **`RepoStore`** (`src/codebase_mcp/ingest.py`) clones a git URL shallowly into `<ingest_root>/<id>/` and offers read-only listing/reading. It executes nothing from the repo. Clones are ephemeral: dropped on `drop_repo` or server shutdown; never stored in the node graph.
- **`Workspace`** gains ingestion methods that compose `RepoStore` reads with `Codebase.define`/`implement`. No new authoritative state beyond the (throwaway) clone directory.
- **`server.py`** binds the new tools and extends the guidance string.

## Configuration (extends `McpConfig`)

- `ingest_root` — where clones live. Env `HAYMANBOT_INGEST_ROOT`, default `<system temp>/haymanbot-ingest`.
- `clone_timeout` — seconds for a clone. Default **120**.
- `allow_clone` — master switch. Env `HAYMANBOT_ALLOW_CLONE`, default **True**; set false to disable all network fetches.

## Tools (added to the core surface)

- `clone_repo(git_url, *, ref=None)` — shallow `git clone --depth 1 --no-tags` (no submodules) into a fresh ephemeral dir; returns `{repo_id, root_files}`. Honors `allow_clone`.
- `repo_tree(repo_id, *, subpath="")` — list files/dirs under the clone (read-only).
- `repo_read(repo_id, path)` — read a file's text from the clone.
- `repo_grep(repo_id, pattern)` — find lines/symbols (ripgrep/Python fallback), to locate candidates.
- `ingest_symbol(repo_id, path, symbol, *, kind="method", name=None, description, tests, parent=None, dependencies=(), tags=(), searchable=True)` — extract the top-level `def`/`class` named `symbol` from `path` (via `ast.get_source_segment`), then `define` a stub and `implement` it with the extracted source + the supplied `tests`. Tests are **required** (the gate). Returns the implement result + new node id.
- `list_repos()` / `drop_repo(repo_id)` — manage ephemeral clones.

## Core flow

1. `clone_repo(url)` → browse with `repo_tree`/`repo_read`/`repo_grep`.
2. For each piece worth keeping: `ingest_symbol(...)` with tests (write tests if the repo's don't transfer cleanly). The symbol's source is built against declared `dependencies`; unresolved imports surface as a build/test failure to iterate on (strip imports, ingest deps first, add them as dependencies) — same loop as normal `implement`.
3. `drop_repo(repo_id)` when done.

## Security model

- Clones are shallow, tagless, submodule-free, ephemeral, and isolated under `ingest_root`; never enter the node store.
- Browsing/reading never executes repo code.
- Execution happens only inside `ingest_symbol`'s `implement` trial-run — the existing sandboxed pytest worker plus the `from build.X` forbidden-import scan — on exactly the one symbol Claude selected.
- `allow_clone=False` disables fetching entirely.
- **Residual risk (documented):** the chosen symbol's code does run in the test worker, which today is process-isolated but not network- or resource-isolated. Hardening (network namespace / no-net during tests, CPU/mem/time limits, optional static review before implement) is follow-up, not v1.

## Testing

- **`RepoStore`** — init a local git repo in a tmp dir (a module with a known function + a test), clone it via a `file://` URL, assert `repo_tree`/`repo_read` return the content; `drop_repo` removes the dir; `allow_clone=False` refuses.
- **symbol extraction** — `extract_symbol(source, name)` returns the exact `def`/`class` segment for top-level symbols; raises `KeyError` for missing/nested ones.
- **`ingest_symbol`** — ingest a function from the fixture clone with passing tests → a node is created and `search` finds it; ingest with failing/too-few tests → rejected, no node; ingest a hidden helper (`searchable=False`) → usable as a dependency, absent from default search.
- **Integration** — `clone_repo` (file:// fixture) → `ingest_symbol` → `search` hit → `run_scratch` importing the ingested node.

## Out of scope (future)

- Auto-extracting a symbol's intra-repo dependency closure (multi-symbol ingest in one call).
- Carrying over the repo's own tests automatically (mapping/rewriting imports).
- Network/resource sandboxing of test runs (hardening).
- Non-git sources (archives, PyPI).
- License/provenance tracking on ingested nodes.
