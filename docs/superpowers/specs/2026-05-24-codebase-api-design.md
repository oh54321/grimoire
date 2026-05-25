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
- **`SearchSystem`** (`src/api/search_system.py`) owns **both** vector indices and their persistence under `<root>/index/`. It has a pure search/index responsibility and **never references `Graph`.** Folder ancestry and object type are baked into each entry's tags at write time (by `Codebase`), so it answers every filter natively with no Graph lookup at query time.
- **`Codebase`** (`src/api/codebase.py`) is the structure around both. It is the **only** object that owns and mutates `Graph` and `SearchSystem`, always in lockstep. It derives `root_id` and `dirty()`, computes each node's composite tags (real ∪ `@kind:` ∪ `@in:` ancestry) from the tree when indexing, and runs the transactional `implement`.

`Graph` and `SearchSystem` never reference each other; all coordination goes through `Codebase`.

## Module layout

```
src/api/
├── __init__.py        # re-exports: Codebase, SearchHit, TagHit, SearchPage, TagPage, ImplementResult, RebuildReport, errors
├── codebase.py        # Codebase facade — owns + coordinates Graph and SearchSystem
├── search_system.py   # SearchSystem — owns the node index + tag index; reindex() rebuilds from the store
├── results.py         # SearchHit, TagHit, SearchPage, TagPage, ImplementResult, RebuildReport dataclasses
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

@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]   # the hits on THIS page (already the lightweight values)
    page: int               # 0-based index of this page
    num_pages: int
    total: int              # total hits across all pages
    page_size: int
    query: str
    def render(self) -> str: ...   # compact text block (see "Rendered pages"); also __str__

@dataclass(frozen=True)
class TagPage:
    hits: list[TagHit]
    page: int
    num_pages: int
    total: int
    page_size: int
    query: str
    def render(self) -> str: ...   # compact text block; also __str__

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

Owns two vector stores from the `search` package. It is Graph-agnostic: folder ancestry and object type are encoded as composite tags on each entry at index time, so every filter is answered by native AND tag queries with no Graph lookup and no post-filtering.

### Indices

All filterable structure — real tags, object type, and folder ancestry — is collapsed into the **tag space** of a single store, so any precise query is one native AND over a tag set and `SearchSystem` needs no Graph knowledge at query time.

- **node index** — a `TaggedKVDatabase` at `<root>/index/nodes/`. One entry per node: `phrase = description` (folders included, so folders are searchable too), `value = {"node_id", "name", "kind", "description"}` (exactly a `SearchHit`, so search never loads a node), and a **composite tag set**:

  ```
  tags(node) = real_tags
             ∪ {"@kind:<object_type>"}                       # e.g. @kind:method, @kind:folder
             ∪ {"@in:<ancestor_id>" for each ancestor folder up to and including root}
  ```

  So "in the subtree of folder F" is simply "has tag `@in:F`", and "is a method" is "has tag `@kind:method`". Ancestry is baked in at write time; the store answers folder/type/tag filters natively with no post-filtering and no Graph lookup.

- **tag index** — a `KVDatabase` at `<root>/index/tags/`. One entry per distinct **real** tag text (never the `@kind:`/`@in:` synthetics): `phrase = value = tag_text`. Backs the "tag search by vector" feature — find tags near a concept even when the wording differs.

**Namespace safety:** the `@` prefix is reserved for synthetic tags; real tag texts are validated to not start with `@` (raises `ApiError`). `Tag.v` (the per-tag vector on nodes) is unused; the tag index embeds tag texts directly via the shared `VectorConverter`, keeping one embedding pathway.

### Public API

```python
class SearchSystem:
    @classmethod
    def open(cls, index_root: Path, embedder=None) -> "SearchSystem": ...

    # mutation — called by Codebase in lockstep with Graph. `tags` here is the
    # COMPOSITE set (real ∪ @kind: ∪ @in:), assembled by Codebase which knows the tree.
    def index_node(self, node_id, name, description, kind, tags: set[str]) -> None
    def remove_node(self, node_id) -> None
    def index_tags(self, real_tags: set[str]) -> None     # adds new real tag texts to the tag vocab
    def update_tags(self, node_id, tags: set[str]) -> None  # rewrite an entry's tag set, NO re-embed

    # query — list form (lower-level; used internally and by tests)
    def search(self, query, *, n=10,
               tags: set[str] = frozenset(),              # real tags — AND (must match ALL)
               object_types: set[str] = frozenset(),      # OR — any of these kinds (empty = any)
               folders: set[NodeId] = frozenset(),        # OR — under any of these folders (empty = whole tree)
               ) -> list[SearchHit]
    def search_tags(self, query, *, n=10) -> list[TagHit]

    # query — paged form (primary surface for Claude; see "Paging and rendered pages")
    def search_page(self, query, *, page=0, page_size=10,
                    tags=frozenset(), object_types=frozenset(), folders=frozenset()) -> SearchPage
    def search_tags_page(self, query, *, page=0, page_size=10) -> TagPage

    # maintenance — `entries` are (node_id, name, description, kind, composite_tags) tuples;
    # Codebase builds them by walking the node store and computing ancestry from the tree
    def reindex(self, entries: Iterable[tuple[NodeId, str, str, str, set[str]]]) -> None
    def list_tags(self) -> set[str]                       # real tags only (TaggedKVDatabase.all_tags filtered)
    def save(self) -> None
```

### Filtering via native AND tags (no post-filter, exact)

`search` composes the filter entirely from tags:

- **real `tags`** → added to the AND set directly.
- **one `object_type` + one `folder`** → add `@kind:<type>` and `@in:<folder>` to the same AND set: a single native `TaggedKVDatabase.search(query, n, tags=…)` call. Exact, HNSW-fast, no scan.
- **OR over several `object_types` and/or `folders`** → run one AND-query per `(folder × type)` combination and merge results by `node_id` keeping the max score, then take the top `n`. Taking the top-`n` from each combination before merging is exact for the union, and both sets are small in practice (≤3 object types; usually one folder), so this is a handful of fast queries.

This needs **no extension to `search` for querying** — it's all native tag AND. The one addition to `TaggedKVDatabase` is **`update_tags(phrase, new_tags)`**, which rewrites an entry's tag maps **without re-encoding the phrase vector** (the existing `add` re-embeds on every call, which would be wasteful for the move/retag path below).

### Paging and rendered pages

Claude works best issuing a query once and flipping through compact pages, so `search_page` / `search_tags_page` are the primary query surface. They reuse the existing `search.pages.PagedList` / `Page` primitives and the stores' existing `search_paged` methods, and add a small result cache so flipping pages never re-embeds or re-queries.

**Caching.** `SearchSystem` holds a bounded LRU (e.g. 16 entries) keyed by `(query, frozenset(tags), frozenset(object_types), frozenset(folders), page_size)`. On a miss it runs the search once (one embedding of `query`) to build a `PagedList[SearchHit]`, caches it, and returns the requested page wrapped in a `SearchPage`. On a hit it just slices out the page — O(1), no embedding, no vector query. The tag side caches `PagedList[TagHit]` the same way.

**Invalidation.** The cache is purely ephemeral and is **cleared on any index mutation** (`index_node`, `remove_node`, `update_tags`, `index_tags`). This keeps it consistent with the single-source-of-truth rule: a stale page can never outlive a write, and the cache holds no authoritative state. An out-of-range `page` raises `IndexError` (from `PagedList.get_page`); page `0` of an empty result is an empty page, not an error.

**Rendered text.** `SearchPage.render()` (and `__str__`) produces a compressed block Claude can read directly — one line of header/footer navigation plus, per hit, the kind, name, a short id to act on, and a truncated description (full text is on the `SearchHit` and via `load`). Illustrative:

```
query: "rolling window average"  ·  page 1/3  ·  showing 1–10 of 27
  1. method  rolling_mean      [n3f2a1b9c4d2]  Streaming mean over a fixed-size window.
  2. class   RingBuffer        [n7a1c0d5e2f8]  Fixed-capacity circular buffer over a list.
  …
  (next page: page=1)
```

`TagPage.render()` is the analogue for tag discovery:

```
query: "stats"  ·  page 1/2  ·  showing 1–10 of 14
  1. statistics    (0.82)
  2. aggregation   (0.71)
  …
```

Descriptions are truncated to a fixed width in the rendering only (the structured `hits` keep the full text). This gives Claude a discover-then-filter-then-page loop entirely in compact text, falling back to `load(node_id)` when it wants the full node.

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

    # ---- (e) navigation — paged, returns rendered-able pages ----
    def search(self, query, *, page=0, page_size=10,
               tags=(), folders=(), object_types=()) -> SearchPage
    def search_tags(self, query, *, page=0, page_size=10) -> TagPage

    # ---- access / introspection (discovery helpers Claude uses to compose filters) ----
    def load(self, node_id) -> Node                  # delegates to Graph.get
    def load_code(self, node_id) -> str
    def load_tests(self, node_id) -> str
    def remove(self, node_id) -> None                # Graph.remove_node + SearchSystem.remove_node
    def list_tags(self) -> set[str]                  # real tags available to filter on
    def children_of(self, node_id) -> set[NodeId]    # tree walk; e.g. to pick a folder to scope a search
    def dirty(self) -> set[NodeId]                   # derived view (see below)
```

### Root bootstrap (point a)

On `open`, `Codebase` asks `Graph` for the parent-less `FolderNode`s. Zero → create one (`new_node_id()`, `name="root"`, `parent_id=None`) and index it. Exactly one → use it. More than one → raise `ApiError` (corrupt tree). `root_id` is never stored; it is this lookup.

When indexing any node, `Codebase` assembles the composite tag set: it reads the node's real tags and computes its ancestor folder ids from the in-memory tree (`@in:<id>` for each, up to root) plus `@kind:<object_type>` (or `@kind:folder`). This is the only place ancestry meets the index, and it's why `SearchSystem` stays Graph-agnostic at query time.

- `make_folder` creates a `FolderNode` under `parent_id` (default `root_id`) via `Graph.add_node`, then `SearchSystem.index_node` with the composite tags and `kind="folder"`. Adding a child also updates the parent folder's `children` set through the Graph.
- `move(node_id, new_parent_id)` re-parents in the Graph: validates the target is a `FolderNode`, that `node_id` is not the root, and that `new_parent_id` is not `node_id` or any descendant of it (cycle check via the in-memory children index). It updates the moved node's `parent_id` and both parents' `children` sets. **It then re-tags the moved subtree in the index:** every descendant's `@in:` ancestor tags above the move point change, so `Codebase` recomputes each affected node's composite tag set and calls `SearchSystem.update_tags` — which rewrites tag maps **without re-embedding**. Cost is O(subtree size) of cheap dict updates; if interrupted, `reindex()` repairs from the tree. Real tags and `@kind:` are unchanged by a move.
- `rename(node_id, new_name)` updates the node and re-indexes it (name is part of the hit and, for code nodes, the exported symbol — `Graph`/`NodeStore` enforces identifier validity). Re-embeds (the value/phrase set changed) but touches only the one node.

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

- `search(query, *, page, page_size, tags, folders, object_types)`: a thin pass-through to `SearchSystem.search_page(...)`. `SearchSystem` translates the filters into composite tags (`@in:<folder>`, `@kind:<type>`) and runs native AND queries — one query for the single-folder/single-type case, a small union of queries for OR over several — building (and caching) a `PagedList[SearchHit]`, then returns the requested `SearchPage`. Real tags are AND, folders and types are OR. No Graph lookup at query time (ancestry is already in the tags); re-embeds only on the first page of a new query/filter combination.
- `search_tags(query, *, page, page_size)`: pass-through to `SearchSystem.search_tags_page` — paged vector search over the real-tag vocabulary, returning a `TagPage` to feed back into `search(tags=...)`. The loop `search_tags` → `list_tags`/`children_of` → `search` (flipping pages by `page=`) is the primary way Claude narrows to a precise tool without scanning the codebase, reading everything as compact rendered text.

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
- **No hot-loop embedding.** Embedding happens only when a node's `phrase` (description) first appears or its name changes — on `define_abstraction` and `rename`. `implement`/`rebuild` never embed, and `move` re-tags via `update_tags` **without** re-embedding. The implement/iterate loop is incremental-build + test-run only.
- **Exact filtering via native tags.** Folder/type/tag filters are composite tags answered by the store's native AND path (exact brute force under threshold); OR over several folders/types is an exact union of per-combination queries. No post-truncation, no query-time Graph lookup.
- **Paged result cache.** Flipping pages of one query is O(1) and embeds nothing — the first page builds and caches the full `PagedList`; later pages slice it. The cache is a small ephemeral LRU cleared on any index mutation, so it holds no authoritative state and never serves a page that predates a write.
- **Honest cost.** The dominant latency in `implement`/`rebuild` is pytest's subprocess startup in the existing `Runner` (hundreds of ms per node), not the build or the search. An in-process / long-lived pytest worker is future work (already noted in the library spec).

## Testing

`tests/api/` mirrors the source. Disk tests use `tmp_path`. The embedder is a small deterministic fake (hash-based vectors) so tests are fast and offline; one slow-marked test exercises the real `VectorConverter` end-to-end.

- **`test_codebase_bootstrap.py`** — first `open` creates exactly one root `FolderNode`; reopening reuses it; a hand-corrupted two-root store raises `ApiError`; `root_id` is the parent-less folder.
- **`test_codebase_folders.py`** — `make_folder` nests under root/explicit parent; `move` re-parents and updates both `children` sets; moving into own subtree / onto a non-folder / moving the root raises `InvalidMove`; after a move, every descendant's `@in:` tags are rewritten so folder-filtered search reflects the new location (and the old folder no longer matches), with no re-embedding.
- **`test_codebase_implement.py`** — `define_abstraction` yields a node that is `dirty()` and searchable with no code; `implement` with passing tests commits and clears dirty; `implement` with a failing test reverts (node back to abstraction-only / prior code), raises `ImplementationFailed` with results, and leaves the node dirty; re-`implement` after a fix commits; a dependency change marks the dependent dirty.
- **`test_codebase_rebuild.py`** — `rebuild` regenerates only dirty nodes (clean ones land in `skipped`); a dependency edit then `rebuild` re-tests dependents; failing nodes appear in `failed` and stay dirty (no revert); subtree-scoped `rebuild(node_id)`.
- **`test_search_system.py`** — `index_node`/`remove_node` round-trip with composite tags; `search` real-tag-AND vs folder/type-OR semantics; the single-folder/single-type case is one query, OR cases union exactly; `@`-prefixed real tags are rejected; `list_tags` excludes synthetics; `search_tags` ranks related tags; `reindex` rebuilds both indices from an entry list; hits carry name/kind/description without loading nodes.
- **`test_search_paging.py`** — `search_page`/`search_tags_page` return correct page slices, `num_pages`/`total`; flipping pages of one query embeds exactly once (assert via a counting fake embedder); out-of-range page raises `IndexError`; empty result is page 0 with no hits; any index mutation clears the cache so the next page reflects the change; `SearchPage.render()`/`TagPage.render()` include kind/name/id/truncated-description and page navigation, and round-trip the listed `node_id`s.
- **`test_codebase_search.py`** — end-to-end: define a few abstractions in nested folders with tags, then `search` with combinations of `tags` / `folders` / `object_types`; folder filter respects subtree membership (descendant matches `@in:<ancestor>`); `search_tags` → `list_tags` → `search(tags=...)` discover-then-filter loop across pages.
- **`test_update_tags.py`** (in `tests/search/`) — the new `TaggedKVDatabase.update_tags` rewrites an entry's tag set, leaves its vector untouched (no re-embed: same phrase still searches identically), and updates tag-filtered results accordingly.

## Future work (explicitly not in this spec)

- **Concrete export.** Materialize the abstract tree into a real named-file project (folders → directories, code nodes → named `.py` files with real relative imports), regenerated incrementally.
- **Forced/auto refactor.** Policies such as splitting a folder when it exceeds a size threshold, or flagging god-nodes.
- **In-process pytest worker** to remove subprocess startup from the implement/iterate loop.
- **Concurrency** (multiple writers); current design is single-threaded like the library.
- **Embedding name + description** (vs description only) and tuning the brute-force threshold once corpus sizes are known.
