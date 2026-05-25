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
5. **Implement** it — supply `code` and `tests`. The api builds + tests the candidate in scratch (imports to dependencies are generated here) and **writes it into the node only if every test passes**; otherwise the node is left untouched and it raises with the failure detail so Claude can iterate. Iterating is cheap: the build is incremental and no re-embedding happens.
6. **Rebuild** at any time to incrementally regenerate + revalidate everything currently dirty (e.g. after a dependency changed), reusing the hash-incremental Builder and the Runner.

## Architecture: three objects

```
Codebase  ── owns ──▶  Graph         (library: node store + cache + builder + runner + in-memory tree index)
   │
   └────── owns ──▶  SearchSystem    (api: two vector indices over the node corpus and the tag vocabulary)
```

- **`Graph`** (existing, `src/library/`) owns the node store, cache, incremental builder, pytest runner, and the in-memory parent/children/tags/dependents index. It gains a few **purely additive** methods (`trial_run`, `iter_code_ids`, `is_build_stale`) plus `Builder.build_trial`; the `Runner` is upgraded to a warm worker (with a one-shot fallback flag). No existing behavior changes.
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

- **node index** — a `TaggedKVDatabase` at `<root>/index/nodes/`. One entry per node, **keyed by `node_id`** (the unique identity) while **embedding the `description`** (folders included, so folders are searchable too); `value = {"node_id", "name", "kind", "description"}` (exactly a `SearchHit`, so search never loads a node), and a **composite tag set**:

  ```
  tags(node) = real_tags
             ∪ {"@kind:<object_type>"}                       # e.g. @kind:method, @kind:folder
             ∪ {"@in:<ancestor_id>" for each ancestor folder up to root (strict ancestors)}
  ```

  Keying by `node_id` is essential: descriptions are **not unique** (two stubs may share text), so the embed text cannot double as the identity. So "in the subtree of folder F" is "has tag `@in:F`" (a folder does not match itself), and "is a method" is "has tag `@kind:method`". Ancestry is baked in at write time; the store answers folder/type/tag filters natively with no post-filtering and no Graph lookup.

- **tag index** — a `KVDatabase` at `<root>/index/tags/`. One entry per distinct **real** tag text (never the `@kind:`/`@in:` synthetics): `phrase = value = tag_text` (tag texts are unique, so phrase-as-key is fine here). Backs the "tag search by vector" feature — find tags near a concept even when the wording differs.

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

`search` composes the filter as a **conjunction of disjunctions** (CNF) over tags and runs it as **one** vector query — one embedding, one search, no matter how many folders or types:

```
must match ALL of:  real_tags                              (AND)
                    {@in:f1, @in:f2, …}  if folders given   (OR within the group)
                    {@kind:t1, @kind:t2, …} if types given  (OR within the group)
```

The candidate id set is computed from the store's tag→id buckets with plain set operations (intersect the AND tags' buckets, intersect with the union of each OR group's buckets), then a single vector search ranks that candidate set. For the common single-folder/single-type case the groups are singletons and it's an ordinary AND query. Results are exact: under the store's `brute_force_threshold` the candidate set is ranked by exact brute-force cosine; above it, HNSW with a membership filter.

This needs four small, principled additions to `TaggedKVDatabase`, all public (the api layer never touches privates). They bump the tagged store to **v3** (the `key`/identity split changes the persisted shape):

- **keyed identity** — `add(phrase, value, tags=(), *, key=None)` where `key` (default the phrase) is the unique dedup identity; the entry still *embeds* `phrase`. The api passes `key=node_id`, `phrase=description`. Internally the dedup map is keyed by `key`, not by phrase.
- **`delete(key)`** — remove an entry by identity (marks the HNSW id deleted and drops the maps). Required by `SearchSystem.remove_node`; the store has no delete today.
- **`update_tags(key, new_tags)`** — rewrite an entry's tag maps **without re-encoding the vector** (the existing `add` re-embeds on every call, wasteful for the move/retag path).
- **`search_filtered(phrase, n, *, all_tags=(), any_groups=())`** — one query whose candidates must contain every tag in `all_tags` and, for each group in `any_groups`, at least one tag from that group. Embeds the phrase once and runs the existing exact/HNSW path over the computed candidate set. (`any_groups=()` makes it identical to today's `search`.) **Critically, the query is embedded exactly once per call** — this is what keeps OR-filtered search as fast as a plain one.

`KVDatabase` (the tag-vocabulary index) is unchanged: its `key` defaults to the phrase, which is unique for tag texts.

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

`implement(node_id, code, tests)` uses a **staged atomic commit** — the candidate code is built and tested in the regenerable `build/` scratch area and only written into the canonical node dir if every test passes. The node dir therefore *never* holds unvalidated code, even across a crash, and a re-implement never overwrites the prior passing version until its replacement is green. No in-memory snapshot/revert is needed.

1. Ensure the declared dependencies are built from canonical sources (`ensure_built(dep)` for each — incremental, usually a no-op).
2. **Trial build + test** via `Graph.trial_run(node_id, code, tests)`: the `Builder` materializes `build/<id>.py` and `build/test_<id>.py` from the *candidate* text (generating the `from build.<dep> import <name>` preamble from the node's declared dependencies — *import assignment is part of generation*), and the `Runner` runs pytest. This reads the node's `meta` for deps/name but **writes nothing to the node dir** and does not record a manifest entry.
3. **All passing** → commit: write `code`/`tests` into the node dir via `Graph.update_node` (atomic tmp+`os.replace`) and `ensure_built` to record the manifest. Fold passing statuses onto the node. Return `ImplementResult(all_passing=True, results)`. (Dependents aren't eagerly touched — the Builder's hash check marks them stale on their next build, which is what surfaces them as dirty.)
4. **Any failing, or a `BuildError`** → discard: invalidate the node's manifest entry (so the next `ensure_built` restores the canonical materialization from prior code, if any) and raise `ImplementationFailed(node_id, results, detail)` with the per-test results. The node dir is untouched — for a first implementation it stays abstraction-only (and dirty); for a re-implement it keeps its prior passing code.

The canonical graph therefore only ever holds passing implementations, even under a crash mid-test. Iteration is fast: a trial rebuilds only this node (deps are already built), and **nothing is re-embedded** because name/description/tags are unchanged.

This needs one small additive method on the library's `Builder` — `build_trial(node_id, code_text, tests_text, dependencies)`, which composes the build/test files from given text without reading or writing the store and without touching the manifest — surfaced through `Graph.trial_run`. Existing `Builder`/`Graph` behavior is unchanged; these are pure additions.

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

- `search(query, *, page, page_size, tags, folders, object_types)`: a thin pass-through to `SearchSystem.search_page(...)`. `SearchSystem` translates the filters into a single CNF tag query (`all_tags` = real tags; `any_groups` = the `@in:` folder group and/or the `@kind:` type group) via `search_filtered`, building (and caching) a `PagedList[SearchHit]`, then returns the requested `SearchPage`. Real tags are AND, folders and types are OR. `page_size` is per call, so Claude chooses how many results per page. One embedding and one vector search per query; no Graph lookup at query time; page flips re-use the cached `PagedList`.
- `search_tags(query, *, page, page_size)`: pass-through to `SearchSystem.search_tags_page` — paged vector search over the real-tag vocabulary, returning a `TagPage` to feed back into `search(tags=...)`. The loop `search_tags` → `list_tags`/`children_of` → `search` (flipping pages by `page=`) is the primary way Claude narrows to a precise tool without scanning the codebase, reading everything as compact rendered text.

## Test execution: warm pytest worker

The dominant latency in `implement`/`rebuild` is pytest's per-call interpreter + plugin startup (~0.3–1 s). This spec replaces the one-shot-subprocess `Runner` with a **long-lived warm worker process** that pays that startup once and then runs each node's tests in roughly the test's own execution time plus light collection (tens of ms). It is a library-layer change (`src/library/runner.py`), used transparently by `Graph.run_tests` and `Graph.trial_run`.

**Why a worker process, not in-host execution.** Running tests inside the host process would be fastest but throws away the subprocess isolation the library chose on purpose — a segfaulting or hanging test would take down the whole `Codebase`. A dedicated worker keeps that isolation: a crash kills only the worker, which is respawned, and that one run is reported as an error rather than propagating.

**Design.**
- **`TestWorker`** lazily spawns one warm child process (`python -m library._test_worker`) on first use and reuses it. The child imports `pytest` once, with `cwd` = store root and the build dir importable, so `import build.<id>` resolves.
- **Protocol.** The parent sends a request line (`{node_id, target_file}`); the child runs `pytest` on `build/test_<id>.py` and replies with the parsed per-test outcomes as one JSON line. Parsing logic is shared with the existing one-shot path so results are identical.
- **Module freshness — the critical correctness point.** Built files change between runs, so before every run the worker evicts every `sys.modules` entry under the `build` package (and the `test_<id>` module). The freshly materialized code is therefore always re-imported; a stale cached module can never be served. `-p no:cacheprovider` disables pytest's own cross-run cache.
- **Robustness.** A per-run timeout guards hangs: on timeout or a dead pipe the parent kills and respawns the worker and surfaces that run as a `BuildError` (never a host crash, never data touched — `implement` simply treats it as a failed trial and leaves the node untouched). The worker is also recycled after N runs to bound any pytest global-state drift.
- **Fallback.** A `LibraryConfig.use_test_worker` flag (default `True`) falls back to the original one-shot subprocess path for debugging or environments where the worker misbehaves; behavior and results are identical, only slower.

This keeps the create→test→fix loop and `rebuild` bound by actual test execution time rather than process startup, while preserving crash isolation and the staged-commit guarantees.

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
- **One embed, one search per query.** A search embeds the query exactly once (~5–30 ms CPU) and runs a single vector search; CNF tag filtering (AND of OR-groups) computes the candidate set with set ops over tag buckets, so OR-over-folders/types costs no extra embeddings or queries. The model lazy-loads once (~1–2 s) on the first query of a process, then stays warm.
- **Exact filtering via native tags.** Folder/type/tag filters are composite tags; the candidate set is ranked by exact brute-force cosine under `brute_force_threshold` (covers expected corpus sizes), HNSW-with-filter above it. No post-truncation, no query-time Graph lookup.
- **Paged result cache.** Flipping pages of one query is O(1) and embeds nothing — the first page builds and caches the full `PagedList`; later pages slice it. The cache is a small ephemeral LRU cleared on any index mutation, so it holds no authoritative state and never serves a page that predates a write.
- **Staged atomic commit.** `implement` builds + tests the candidate in the regenerable `build/` area and writes it into the node dir only on success, so the canonical store never holds unvalidated code (even across a crash) and a re-implement never clobbers the prior passing version. No snapshot/revert; on failure the node dir is simply untouched.
- **Saving is fast and robust.** A commit is a few small atomic writes (`code.py`, `tests.py`, `meta.json` via tmp+`os.replace`) plus O(1) cache invalidation and no embedding. A crash between those post-validation writes leaves validated-content files that may be momentarily inconsistent → the node reads as dirty and is re-tested on next `rebuild`; never silent-bad, never corrupt.
- **Test run via warm worker.** Build, save, search, and index updates are all sub-ms-to-low-ms. Test execution uses the long-lived warm pytest worker (see "Test execution"), so each run costs ≈ the test's own time + light collection rather than ~0.3–1 s of process startup. The build itself is minimal (content-hash incremental — only changed nodes + dependents re-materialize). The remaining lever for broad refactors is parallel test runs across independent nodes (future work).

## Testing

`tests/api/` mirrors the source. Disk tests use `tmp_path`. The embedder is a small deterministic fake (hash-based vectors) so tests are fast and offline; one slow-marked test exercises the real `VectorConverter` end-to-end.

- **`test_codebase_bootstrap.py`** — first `open` creates exactly one root `FolderNode`; reopening reuses it; a hand-corrupted two-root store raises `ApiError`; `root_id` is the parent-less folder.
- **`test_codebase_folders.py`** — `make_folder` nests under root/explicit parent; `move` re-parents and updates both `children` sets; moving into own subtree / onto a non-folder / moving the root raises `InvalidMove`; after a move, every descendant's `@in:` tags are rewritten so folder-filtered search reflects the new location (and the old folder no longer matches), with no re-embedding.
- **`test_codebase_implement.py`** — `define_abstraction` yields a node that is `dirty()` and searchable with no code; `implement` with passing tests commits and clears dirty; `implement` with a failing test raises `ImplementationFailed` with results and leaves the node dir **untouched** (abstraction-only for a first attempt; prior passing `code.py`/`tests.py` intact for a re-implement) and dirty; the node dir never contains unvalidated code (assert `load_code` after a failed first implement is still empty); re-`implement` after a fix commits; a dependency change marks the dependent dirty.
- **`test_builder_trial.py`** (in `tests/library/`) — `Builder.build_trial` materializes build/test files from given text without writing the store or recording a manifest entry; a failed trial followed by `ensure_built` restores the canonical materialization; `Graph.trial_run` returns results without committing.
- **`test_test_worker.py`** (in `tests/library/`) — warm worker reuses one process across runs; after rebuilding a node's code the worker picks up the new behavior (stale-module eviction works); a crashing/hanging test is reported as a `BuildError` and the worker is respawned (host survives); results are identical to the one-shot path (`use_test_worker=False`).
- **`test_codebase_rebuild.py`** — `rebuild` regenerates only dirty nodes (clean ones land in `skipped`); a dependency edit then `rebuild` re-tests dependents; failing nodes appear in `failed` and stay dirty (no revert); subtree-scoped `rebuild(node_id)`.
- **`test_search_system.py`** — `index_node`/`remove_node` round-trip with composite tags, keyed by `node_id`; **two nodes with identical descriptions are both indexed and independently found/removed** (collision guard); `search` real-tag-AND vs folder/type-OR semantics; OR over multiple folders/types embeds exactly once (counting fake embedder) and matches the union exactly; `@`-prefixed real tags are rejected; `list_tags` excludes synthetics; `search_tags` ranks related tags; `reindex` rebuilds both indices from an entry list; hits carry name/kind/description without loading nodes.
- **`test_search_paging.py`** — `search_page`/`search_tags_page` return correct page slices, `num_pages`/`total`; flipping pages of one query embeds exactly once (assert via a counting fake embedder); out-of-range page raises `IndexError`; empty result is page 0 with no hits; any index mutation clears the cache so the next page reflects the change; `SearchPage.render()`/`TagPage.render()` include kind/name/id/truncated-description and page navigation, and round-trip the listed `node_id`s.
- **`test_codebase_search.py`** — end-to-end: define a few abstractions in nested folders with tags, then `search` with combinations of `tags` / `folders` / `object_types`; folder filter respects subtree membership (descendant matches `@in:<ancestor>`); `search_tags` → `list_tags` → `search(tags=...)` discover-then-filter loop across pages.
- **`test_tagged_kvdb_keyed.py`** (in `tests/search/`) — `add` with an explicit `key` dedups on the key, not the phrase: two entries with identical phrases but different keys both persist and are independently retrievable; re-adding the same key replaces; `delete(key)` removes an entry (gone from search and tag listings); a v2 store file is rejected with a clear version error (v3).
- **`test_update_tags.py`** (in `tests/search/`) — `TaggedKVDatabase.update_tags(key, tags)` rewrites an entry's tag set, leaves its vector untouched (no re-embed: same phrase still searches identically), and updates tag-filtered results accordingly.
- **`test_search_filtered.py`** (in `tests/search/`) — `TaggedKVDatabase.search_filtered` honors `all_tags` (AND) plus each `any_groups` group (OR), embeds the phrase exactly once per call (counting fake embedder), equals plain `search` when `any_groups=()`, and is exact under the brute-force threshold.

## Future work (explicitly not in this spec)

- **Concrete export.** Materialize the abstract tree into a real named-file project (folders → directories, code nodes → named `.py` files with real relative imports), regenerated incrementally.
- **Forced/auto refactor.** Policies such as splitting a folder when it exceeds a size threshold, or flagging god-nodes.
- **Parallel test runs** across independent nodes during `rebuild` (e.g. a small pool of warm workers), to cut broad-refactor revalidation time.
- **Incremental `dirty()`** maintained in memory if the per-call scan ever shows up at very large scale.
- **Concurrency** (multiple writers); current design is single-threaded like the library.
- **Embedding name + description** (vs description only) and tuning the brute-force threshold once corpus sizes are known.
