# Codebase API: Build / Refactor / Navigate — Design

**Date:** 2026-05-24
**Status:** Approved (pending user review)
**Scope:** A new `src/api/` package that sits on top of the existing `library` (`Graph`) and `search` (`TaggedKVDatabase`) packages and gives Claude one high-level object — `Codebase` — for building abstractions and implementations, organizing them into folders, incrementally regenerating the codebase, and navigating it by vector search with tag / folder / type filters.

## Goal

Give Claude a single, robust surface for growing a codebase as a graph of nodes: define an abstraction (the spec), implement it (code + tests, built and validated on the spot), organize nodes into folders, and find existing nodes by meaning. The api layer coordinates the two lower packages; it adds no new authoritative on-disk state of its own.

This is the "Claude browses and builds the library" surface that the library node-store spec (`2026-05-23-library-node-store-design.md`) explicitly deferred.

Out of scope for this spec (see Future work): exporting the node tree to a concrete named-file project tree; forced/automatic refactors when a folder exceeds a size threshold; an in-process pytest worker.

## Design principle: one source of truth

The single most important property of this layer is that the **on-disk node store (`<root>/<node_id>/`) is the only authoritative state.** Everything else is a regenerable cache:

- the Builder manifest (`<root>/build/_manifest.json`) — build staleness via content hashes;
- the two vector indices (`<root>/index/`) — a searchable denormalized copy of each node's name / description / kind / tags.

There is **no separate `api_state.json`.** Two pieces of state that a naive design would persist separately are instead *derived*:

- **`root_id`** — the unique `FolderNode` whose `parent_id is None`. Created on first `open`; it is an error for two to exist.
- **the dirty set** ("which nodes have been modified and need regenerating") — computed, not stored: a node is dirty when the Builder reports its build stale (no manifest entry, or its own / a dependency's code hash changed) **or** its persisted test statuses are not all passing. Both inputs are already persisted (the manifest and `meta.json`), so the information is durable without a fourth file that could diverge on a crash.

Because every non-authoritative store is regenerable, cross-file divergence after a crash is self-healing rather than a correctness bug: `SearchSystem.reindex()` rebuilds the indices from the node store, and deleting `<root>/build/` forces a clean rebuild.

## Intended workflow

1. **Open** the codebase at a root path. The single root folder is created if absent.
2. **Navigate / search** for existing nodes by meaning — `search_tags` to discover relevant tags, then `search` with tag / folder / type filters. Results are lightweight hits (id, name, kind, description, score); the full node is loaded only on demand.
3. **Make folders** and **move** nodes to organize the tree.
4. **Define an abstraction** — name, description, object type, dependencies, tags. No code yet. The node is immediately searchable and shows up as dirty (needs implementation).
5. **Implement** it — supply `code` and `tests`. The api builds the node (imports to dependencies are generated here), runs the tests, and **commits only if every test passes**; otherwise it reverts and raises with the failure detail so Claude can iterate. Iterating is cheap: the build is incremental and no re-embedding happens.
6. **Rebuild** at any time to incrementally regenerate + revalidate everything currently dirty (e.g. after a dependency changed), reusing the hash-incremental Builder and the Runner.

## Architecture: three objects

```
Codebase  ── owns ──▶  Graph         (library: node store + cache + builder + runner + in-memory tree index)
   │
   └────── owns ──▶  SearchSystem    (api: two vector indices over the node corpus and the tag vocabulary)
```

- **`Graph`** (existing, `src/library/`, unchanged) owns the node store, cache, incremental builder, pytest runner, and the in-memory parent/children/tags/dependents index.
- **`SearchSystem`** (`src/api/search_system.py`) owns **both** vector indices and their persistence under `<root>/index/`. It has a pure search/index responsibility and **never references `Graph`.** It cannot resolve folder ancestry itself; the coordinator supplies that as a precomputed id set.
- **`Codebase`** (`src/api/codebase.py`) is the structure around both. It is the **only** object that owns and mutates `Graph` and `SearchSystem`, always in lockstep. It derives `root_id` and `dirty()`, resolves folder filters into id sets from `Graph`, and runs the transactional `implement`.

`Graph` and `SearchSystem` never reference each other; all coordination goes through `Codebase`.

## Module layout

```
src/api/
├── __init__.py        # re-exports: Codebase, SearchHit, TagHit, ImplementResult, RebuildReport, errors
├── codebase.py        # Codebase facade — owns + coordinates Graph and SearchSystem
├── search_system.py   # SearchSystem — owns the node index + tag index; reindex() rebuilds from the store
├── results.py         # SearchHit, TagHit, ImplementResult, RebuildReport dataclasses
└── errors.py          # ApiError, ImplementationFailed, InvalidMove

tests/api/             # mirrors source layout
```

### On-disk layout (additions to what `library` already writes)

```
<root>/
├── config.json          # existing (LibraryConfig)
├── <node_id>/ ...        # existing nodes (meta.json, code.py, tests.py) — AUTHORITATIVE
├── build/ ...            # existing materialized build + _manifest.json (regenerable cache)
└── index/                # NEW — vector indices (regenerable cache)
    ├── nodes/            # TaggedKVDatabase: store.json + index.bin
    └── tags/             # KVDatabase: store.json + index.bin
```

No `api_state.json`.

## Data model

The api layer adds **no new node types**; it uses `FolderNode` and `CodeNode` from `library.nodes` as-is. It adds only result/transport dataclasses in `results.py`:

```python
@dataclass(frozen=True)
class SearchHit:
    node_id: NodeId
    name: str
    kind: str          # CodeNode.object_type ("class"|"method"|"executable") or "folder"
    description: str
    score: float       # cosine similarity in [-1, 1]

@dataclass(frozen=True)
class TagHit:
    tag: str
    score: float

@dataclass
class ImplementResult:
    node_id: NodeId
    results: list[TestResult]   # from library.runner; all PASSING on a returned result
    all_passing: bool           # always True when returned (failure path raises)

@dataclass
class RebuildReport:
    rebuilt: list[NodeId]       # nodes whose build was (re)materialized this call
    passed: list[NodeId]        # dirty nodes that ended all-passing
    failed: list[NodeId]        # dirty nodes that still have failing/un-run tests
    skipped: list[NodeId]       # nodes considered but already clean
```

`kind` collapses folder-vs-code so a hit is fully self-describing without loading the node: `"folder"` for `FolderNode`, else the `CodeNode.object_type`.

## `SearchSystem`

Owns two vector stores from the `search` package and the maps needed for exact filtering. It is Graph-agnostic: callers pass an `allowed_ids` set (already resolved to the folder subtree) and an `object_types` set; `SearchSystem` intersects those with its tag filter and the live corpus and queries.

### Indices

- **node index** — a `TaggedKVDatabase` at `<root>/index/nodes/`. One entry per node: `phrase = description` (folders included, so folders are searchable too), `tags = {t.text for t in node.tags}`, `value = {"node_id", "name", "kind", "description"}`. The value is exactly what a `SearchHit` needs, so search never loads a node.
- **tag index** — a `KVDatabase` at `<root>/index/tags/`. One entry per distinct tag text: `phrase = value = tag_text`. This backs the "tag search by vector" feature — find tags near a concept even when the wording differs.

`Tag.v` (the per-tag vector already stored on nodes) is not used by the index; the tag index embeds tag texts directly via the shared `VectorConverter`, keeping one embedding pathway.

### Public API

```python
class SearchSystem:
    @classmethod
    def open(cls, index_root: Path, embedder=None) -> "SearchSystem": ...

    # mutation — called by Codebase in lockstep with Graph
    def index_node(self, node_id, name, description, kind, tags: set[str]) -> None
    def remove_node(self, node_id) -> None
    def index_tags(self, tags: set[str]) -> None          # adds any new tag texts to the tag vocab

    # query
    def search(self, query, *, n=10,
               tags: set[str] = frozenset(),              # AND — must match ALL
               object_types: set[str] = frozenset(),      # OR  — kind in this set (empty = any)
               allowed_ids: set[NodeId] | None = None,     # OR over folders, precomputed by Codebase
               ) -> list[SearchHit]
    def search_tags(self, query, *, n=10) -> list[TagHit]

    # maintenance — `entries` are (node_id, name, description, kind, tags) tuples,
    # the same fields index_node takes; Codebase builds them by walking the node store
    def reindex(self, entries: Iterable[tuple[NodeId, str, str, str, set[str]]]) -> None
    def save(self) -> None
```

### Exact filtering (no over-fetch-and-truncate)

The three filters have different logic — tags are AND, folders and types are OR — and the underlying `TaggedKVDatabase` natively supports only AND-tag filtering. Rather than over-fetch and post-truncate (which can silently drop matches), `SearchSystem` resolves a **single exact allowed-id set** and hands it to the store:

1. Start from the tag-AND candidate set (`TaggedKVDatabase`'s native filter) or "all live" when no tags.
2. Intersect with `allowed_ids` (the folder-subtree set, OR over the requested folders) when not `None`.
3. Intersect with the set of node ids whose `kind ∈ object_types` when `object_types` is non-empty (`SearchSystem` keeps a `kind → {node_id}` map). The folder-and-type intersection becomes the `allowed_values` set; tags are passed through and AND-intersected by the store.
4. Run the vector query constrained to that final id set, returning the true top-`n`.

For typical filtered queries the candidate set is small, so the store's existing **exact brute-force path** (already used below its `brute_force_threshold`) runs — exact and fast — and only large unfiltered queries fall back to approximate HNSW. This requires one small, principled addition to the `search` package (see next section) so the api layer never reaches into private internals.

### Small extension to `search.TaggedKVDatabase`

`TaggedKVDatabase` already computes a tag-filter id set and feeds it to a private `_search_locked(vec, n, allowed)`. We add a **public** method that accepts an externally supplied allowed-id set (in `node_id`/value terms), intersects it with the tag filter, and runs the same path:

```python
def search_within(self, phrase, n, *, tags=(), allowed_values: set | None = None) -> list[tuple[JSONValue, float]]
```

This keeps folder/type filtering exact and reuses the tested search path; the api layer depends only on a public method. (Translating `node_id` values to the store's internal int ids uses the store's existing `phrase_to_id` / value maps; the method lives in `search` precisely so that translation stays encapsulated.)

## `Codebase`

The coordinator and the surface Claude drives.

```python
class Codebase:
    @classmethod
    def open(cls, root: Path, **config_overrides) -> "Codebase":
        """Open Graph(root) and SearchSystem(root/index); ensure the single root
        FolderNode exists; if the indices are empty but nodes exist, reindex()."""

    @property
    def root_id(self) -> NodeId: ...        # derived: the unique parent-less FolderNode

    # ---- (c) folders & moving ----
    def make_folder(self, name, *, parent_id=None, description="", tags=()) -> NodeId
    def move(self, node_id, new_parent_id) -> None
    def rename(self, node_id, new_name) -> None

    # ---- (b) spec first, then implementation ----
    def define_abstraction(self, name, description, object_type, *,
                           parent_id=None, dependencies=(), tags=()) -> NodeId
    def add_method(self, name, description, **kw) -> NodeId       # object_type="method"
    def add_class(self, name, description, **kw) -> NodeId        # object_type="class"
    def add_executable(self, name, description, **kw) -> NodeId   # object_type="executable"
    def implement(self, node_id, code, tests) -> ImplementResult

    # ---- (d) incremental rebuild ----
    def rebuild(self, node_id=None) -> RebuildReport

    # ---- (e) navigation ----
    def search(self, query, *, n=10, tags=(), folders=(), object_types=()) -> list[SearchHit]
    def search_tags(self, query, *, n=10) -> list[TagHit]

    # ---- access / introspection ----
    def load(self, node_id) -> Node                  # delegates to Graph.get
    def load_code(self, node_id) -> str
    def load_tests(self, node_id) -> str
    def remove(self, node_id) -> None                # Graph.remove_node + SearchSystem.remove_node
    def dirty(self) -> set[NodeId]                   # derived view (see below)
```

### Root bootstrap (point a)

On `open`, `Codebase` asks `Graph` for the parent-less `FolderNode`s. Zero → create one (`new_node_id()`, `name="root"`, `parent_id=None`) and index it. Exactly one → use it. More than one → raise `ApiError` (corrupt tree). `root_id` is never stored; it is this lookup.

### Folders & moving (point c)

- `make_folder` creates a `FolderNode` under `parent_id` (default `root_id`) via `Graph.add_node`, then `SearchSystem.index_node(kind="folder")`. Adding a child also updates the parent folder's `children` set through the Graph.
- `move(node_id, new_parent_id)` re-parents in the Graph: validates the target is a `FolderNode`, that `node_id` is not the root, and that `new_parent_id` is not `node_id` or any descendant of it (cycle check via the in-memory children index). It updates the moved node's `parent_id` and both parents' `children` sets. **It does not touch the vector index** — ancestry is never stored there; the folder filter is computed live — so move is cheap and cannot desync search.
- `rename(node_id, new_name)` updates the node and re-indexes it (name is part of the hit and, for code nodes, the exported symbol — `Graph`/`NodeStore` enforces identifier validity).

### Spec → implementation (point b)

`define_abstraction` creates a `CodeNode` with the given metadata and **no `code`/`tests`**, via `Graph.add_node`, then indexes it (`SearchSystem.index_node` + `index_tags`). With no build and no passing tests it is automatically `dirty()`.

`implement(node_id, code, tests)` is **transactional — commit on green, revert on failure:**

1. Snapshot the node's current `meta` + `code.py` + `tests.py` in memory (for a first implementation, the snapshot has no code/tests).
2. Write the new `code` and `tests` via `Graph.update_node` (atomic tmp+`os.replace`); this invalidates the build manifest entry.
3. `Graph.run_tests(node_id)` — which calls `ensure_built` (generating the `from build.<dep> import <name>` preamble from the declared dependencies — *import assignment is part of generation*) and then runs pytest, folding statuses back onto the node.
4. **All passing** → commit: the new code/tests stay; return `ImplementResult(all_passing=True, results)`. (Dependents are not eagerly touched — the Builder's hash check marks them stale automatically on their next build, which is what makes them show up dirty.)
5. **Any failing, or a `BuildError`** → revert: restore the snapshot via `Graph.update_node` (or remove code for a first implementation), invalidate the manifest entry, and raise `ImplementationFailed(node_id, results, detail)` carrying the per-test results.

The canonical graph therefore only ever holds passing implementations. Iteration is fast: re-calling `implement` rebuilds only this node plus any changed dependencies (incremental Builder), and **nothing is re-embedded** because name/description/tags are unchanged.

### Incremental rebuild (point d)

`rebuild(node_id=None)` reconciles the codebase in bulk. With no argument it processes every currently-`dirty()` node; with a `node_id` it processes that node's subtree. For each, in dependency order, it calls `Graph.ensure_built` (hash-incremental — clean nodes are skipped) and `Graph.run_tests`, accumulating a `RebuildReport`.

Unlike `implement`, `rebuild` is a bulk reconcile and **does not revert** on failure — reverting a dependent that legitimately needs updating because a dependency changed would discard valid work. Failing nodes simply remain in `failed` (and stay `dirty()`), for Claude to fix.

### Derived dirty set

```python
def dirty(self) -> set[NodeId]:
    out = set()
    for nid in self._graph.iter_code_ids():
        node = self._graph.get(nid)
        if self._graph.is_build_stale(nid) or not _all_passing(node.tests):
            out.add(nid)
    return out
```

`is_build_stale` is the Builder's existing staleness check (no manifest entry, or code/dep hash changed). `_all_passing` is false when `tests` is empty or any status is not `PASSING`. Both inputs are persisted; the view is cheap (in-memory) and never diverges. (`Graph` gains thin pass-throughs `iter_code_ids()` and `is_build_stale(node_id)` if not already exposed.)

### Navigation (point e)

- `search(query, *, n, tags, folders, object_types)`: `Codebase` resolves `folders` into `allowed_ids` = the union of each listed folder's subtree (BFS over the in-memory children index; `None` when `folders` is empty = no folder constraint), then calls `SearchSystem.search(query, n=n, tags=set(tags), object_types=set(object_types), allowed_ids=allowed_ids)`. Tags are AND (all), folders and types are OR (any). Returns lightweight `SearchHit`s.
- `search_tags(query, *, n)`: straight pass-through to `SearchSystem.search_tags` — vector search over the tag vocabulary, returning `TagHit`s to feed back into `search(tags=...)`.

## Errors (`api/errors.py`)

```python
class ApiError(Exception): ...                                  # base; also raised for a corrupt (multi-root) tree
@dataclass
class ImplementationFailed(ApiError):
    node_id: str
    results: list[TestResult]    # per-test outcomes for debugging
    detail: str                  # first failing line / build-error reason
@dataclass
class InvalidMove(ApiError):
    node_id: str
    target_id: str
    reason: str                  # "into-own-subtree" | "target-not-folder" | "move-root"
```

Library errors (`NodeNotFound`, `DuplicateNodeId`, `BuildError`, `MissingDependency`, `InvalidNodeName`, `DescriptionTooLong`, …) propagate unwrapped from the layer that raises them.

## Consistency, performance, and failure behavior

- **Single source of truth.** Only the node store is authoritative. The manifest and indices are regenerable; `reindex()` and deleting `build/` recover them. No fourth state file exists to diverge.
- **Lockstep mutation.** `Codebase` updates `Graph` then `SearchSystem` on every change. Each underlying write is atomic (tmp + `os.replace`). A crash between the two writes leaves the index stale relative to the store; because the store is truth and the index is regenerable, `reindex()` repairs it without data loss. `open` triggers a reindex when the indices are empty but nodes exist.
- **No hot-loop embedding.** Embedding happens only on `define_abstraction`, `rename`, and tag changes — never on `implement`/`rebuild`. The implement/iterate loop is incremental-build + test-run only.
- **Exact filtering.** Combined tag/folder/type filtering resolves to a single id set queried exactly (brute force under threshold), not approximate post-truncation.
- **Honest cost.** The dominant latency in `implement`/`rebuild` is pytest's subprocess startup in the existing `Runner` (hundreds of ms per node), not the build or the search. An in-process / long-lived pytest worker is future work (already noted in the library spec).

## Testing

`tests/api/` mirrors the source. Disk tests use `tmp_path`. The embedder is a small deterministic fake (hash-based vectors) so tests are fast and offline; one slow-marked test exercises the real `VectorConverter` end-to-end.

- **`test_codebase_bootstrap.py`** — first `open` creates exactly one root `FolderNode`; reopening reuses it; a hand-corrupted two-root store raises `ApiError`; `root_id` is the parent-less folder.
- **`test_codebase_folders.py`** — `make_folder` nests under root/explicit parent; `move` re-parents and updates both `children` sets; moving into own subtree / onto a non-folder / moving the root raises `InvalidMove`; move leaves the index unchanged yet folder-filtered search still reflects the new location.
- **`test_codebase_implement.py`** — `define_abstraction` yields a node that is `dirty()` and searchable with no code; `implement` with passing tests commits and clears dirty; `implement` with a failing test reverts (node back to abstraction-only / prior code), raises `ImplementationFailed` with results, and leaves the node dirty; re-`implement` after a fix commits; a dependency change marks the dependent dirty.
- **`test_codebase_rebuild.py`** — `rebuild` regenerates only dirty nodes (clean ones land in `skipped`); a dependency edit then `rebuild` re-tests dependents; failing nodes appear in `failed` and stay dirty (no revert); subtree-scoped `rebuild(node_id)`.
- **`test_search_system.py`** — `index_node`/`remove_node` round-trip; `search` tag-AND vs folder/type-OR semantics; `allowed_ids` constrains results exactly; `search_tags` ranks related tags; `reindex` rebuilds both indices from a node list; hits carry name/kind/description without loading nodes.
- **`test_codebase_search.py`** — end-to-end: define a few abstractions in nested folders with tags, then `search` with combinations of `tags` / `folders` / `object_types`; folder filter respects subtree membership; `search_tags` → `search(tags=...)` round-trip.
- **`test_search_within.py`** (in `tests/search/`) — the new `TaggedKVDatabase.search_within` honors an external `allowed_values` set intersected with tags, exact under brute force.

## Future work (explicitly not in this spec)

- **Concrete export.** Materialize the abstract tree into a real named-file project (folders → directories, code nodes → named `.py` files with real relative imports), regenerated incrementally.
- **Forced/auto refactor.** Policies such as splitting a folder when it exceeds a size threshold, or flagging god-nodes.
- **In-process pytest worker** to remove subprocess startup from the implement/iterate loop.
- **Concurrency** (multiple writers); current design is single-threaded like the library.
- **Embedding name + description** (vs description only) and tuning the brute-force threshold once corpus sizes are known.
