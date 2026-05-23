# Library: Node Graph + Disk Store + Cache — Design

**Date:** 2026-05-23
**Status:** Approved (pending user review)
**Scope:** Split the current monolithic `src/library/graph.py` into focused modules; implement a persistent node store with a bounded in-memory cache; and add an incremental builder and pytest-based test runner.

## Goal

Give Claude a place to incrementally build up a graph of code abstractions and their implementations. The graph supports local exploration by ID (you can navigate without loading everything), the store persists both node metadata and code to disk, a bounded in-memory cache makes repeated lookups cheap, and an incremental builder materializes nodes into importable Python on demand so their tests can be run.

Out of scope for this spec: vector-based tag similarity search using `Tag.v`, and a higher-level "search by tag / filesystem" UX layer (that will sit on top of `Graph.find_by_tag` and `iter_ids` later).

## Intended workflow

The primary consumer is Claude, building new tools on top of existing ones. The expected interaction loop:

1. **Browse cheaply.** Search the graph via tag text or directory-style navigation (e.g. `find_by_tag`, `children_of`). These hit the in-memory index only.
2. **Read metadata, not code.** Pull `name` + `description` for candidate nodes via `Graph.get(node_id)`. Loaded into the byte-budgeted cache; never pays for code text.
3. **Pick dependencies.** Decide which nodes the new abstraction needs.
4. **Write new code.** Author `code.py` (and optionally `tests.py`) for the new node. The code refers to deps by their bare symbol names (`rolling_sum(...)`) and **does not** write `from build.X import ...` lines for them.
5. **Declare deps in metadata.** Create the `CodeNode` with the chosen IDs in `dependencies`. Save via `Graph.add_node(node, code=..., tests=...)`.
6. **Build & test.** `Graph.run_tests(node_id)` materializes the node (with generated imports prepended), runs pytest, and folds results back into the node.

This loop is what shapes the design: cheap browsing demands the metadata/code split in the cache; graph-driven imports demand the Builder's preamble generation; incremental builds demand the manifest; pytest-by-subprocess demands the Runner.

## Architecture

Four layers with one facade:

- **`NodeStore`** owns disk I/O. It reads and writes the on-disk representation of a single node (`meta.json`, `code.py`, `tests.py`). Nothing else touches the source filesystem.
- **`NodeCache`** wraps a `NodeStore` and adds LRU + TTL eviction in front of it. Reads fall through to the store on miss; writes are write-through (disk first, cache second) so the cache is always a subset of disk.
- **`Builder`** materializes built code into a separate `<root>/build/` directory so nodes can be imported. It tracks per-node code+dependency content hashes in a manifest and only rebuilds nodes whose hash (or any dep's hash) has changed. The source tree under `<root>/<node_id>/` is authoritative; the build directory is regenerable.
- **`Runner`** invokes pytest as a subprocess against the built tree, parses results, and updates each `Test.status` on the node.
- **`Graph`** is the public facade. It holds a `NodeCache`, a `Builder`, a `Runner`, and an in-memory index of IDs and edges (parent, children, tags, reverse-deps) so navigation queries don't hit the cache or the disk.

The index is rebuilt from disk truth on `Graph.open`. The build manifest is similarly rebuildable: deleting `<root>/build/` and reopening the graph forces a fresh build on next `ensure_built`.

## Module layout

```
src/library/
├── __init__.py       # public re-exports (Graph, Node types, errors)
├── nodes.py          # Tag, Node, FolderNode, CodeNode, Test, TestStatus, ObjectType
├── ids.py            # NodeId type alias + new_node_id() (uuid4 hex, shortened)
├── errors.py         # NodeNotFound, DuplicateNodeId, CorruptMetaFile, DescriptionTooLong, InvalidNodeName, BuildError, MissingDependency
├── config.py         # LibraryConfig dataclass + load/save to store/config.json
├── tokens.py         # count_tokens(text, encoding) using tiktoken
├── store.py          # NodeStore (disk I/O: meta.json, code.py, tests.py)
├── cache.py          # NodeCache (LRU + TTL)
├── builder.py        # Builder: incremental materialization into <root>/build/
├── runner.py         # Runner: pytest subprocess + result parsing
└── graph.py          # Graph facade + private in-memory index

tests/library/        # mirrors source layout
```

External dependencies added: `tiktoken`, `numpy` (already implied by `Tag.v`).

## Data model (`nodes.py`)

All node types are `@dataclass`es so equality, repr, and serialization are uniform. Refactors from the existing skeleton:

- `Buildable` is removed entirely. Build state is no longer carried on the node — the Builder's manifest is the source of truth for "is this node built and against which dep hashes."
- `CodeImplementation` is removed — its fields are already covered by `CodeNode` plus the store layout.
- `Test` has a `name` (matches the pytest function name without the `test_` prefix, used to correlate results back to the node) and a `TestStatus` enum (`unrun` / `passing` / `failing`). The actual test code lives in `<root>/<node_id>/tests.py`, not on the dataclass.
- `Node` itself becomes a dataclass base with the shared fields (`node_id`, `parent_id`, `name`, `description`, `tags`).
- For `CodeNode`s, `name` doubles as the exported Python symbol (see Builder § Generated imports) and must be a valid Python identifier. FolderNode `name`s have no such constraint — they're human labels only. `NodeStore.save` validates this and raises `InvalidNodeName` on failure.

```python
NodeId = str
ObjectType = Literal["class", "method", "executable"]

class TestStatus(Enum):
    UNRUN = "unrun"
    PASSING = "passing"
    FAILING = "failing"

@dataclass(frozen=True, eq=False)
class Tag:
    text: str
    v: np.ndarray   # persisted but unused by the index in v1

    # Hash/equality are by `text` only — ndarrays aren't hashable and we don't want
    # two tags with the same text but different vectors to count as distinct.
    def __hash__(self) -> int: return hash(self.text)
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Tag) and other.text == self.text

@dataclass
class Test:
    name: str                              # matches pytest function name sans `test_`
    status: TestStatus = TestStatus.UNRUN

@dataclass
class Node:
    node_id: NodeId
    name: str
    description: str
    parent_id: NodeId | None = None
    tags: set[Tag] = field(default_factory=set)

@dataclass
class FolderNode(Node):
    children: set[NodeId] = field(default_factory=set)
    node_type: ClassVar[str] = "folder"

@dataclass
class CodeNode(Node):
    dependencies: set[NodeId] = field(default_factory=set)
    object_type: ObjectType = "method"
    tests: list[Test] = field(default_factory=list)
    node_type: ClassVar[str] = "code"
```

## IDs (`ids.py`)

```python
NodeId = str

def new_node_id() -> NodeId:
    """Return a short hex id (first 12 chars of uuid4)."""
```

Short enough to type, long enough to avoid collisions at expected scale.

## Errors (`errors.py`)

- `NodeNotFound(node_id)` — store lookup for a missing id.
- `DuplicateNodeId(node_id)` — `add_node` with an id that already exists.
- `CorruptMetaFile(node_id, reason)` — `meta.json` is missing required fields or unparseable.
- `DescriptionTooLong(node_id, actual_tokens, limit)` — save rejected by the token-budget check.
- `InvalidNodeName(node_id, name)` — CodeNode name is not a valid Python identifier.
- `MissingDependency(node_id, missing_dep_id)` — builder couldn't resolve a declared dependency.
- `BuildError(node_id, reason)` — build failed for any other reason (disallowed `from build.X` import in `code.py`/`tests.py`, duplicate dep symbol, etc.).

The facade does not wrap or translate these; they propagate as-is.

## Config (`config.py`)

```python
@dataclass(frozen=True)
class LibraryConfig:
    root_path: Path
    max_cache_mb: int = 50
    ttl_seconds: float = 3600.0
    max_description_tokens: int = 200
    tokenizer_encoding: str = "cl100k_base"

    @classmethod
    def load(cls, root: Path) -> "LibraryConfig":
        """Read root/config.json; fall back to defaults if missing."""

    def save(self) -> None:
        """Write self to root/config.json."""
```

`Graph.open(root, **overrides)` reads the on-disk config, applies any kwarg overrides, persists the merged result back to `config.json`, then builds the store/cache/index. This way each store remembers its own settings and a caller doesn't have to remember them on every open.

## Token counting (`tokens.py`)

```python
def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Count tokens using tiktoken. Encoder instances cached at module level."""
```

Pure function, no I/O. Uses `tiktoken.get_encoding(encoding)`, cached in a module-level dict. tiktoken uses OpenAI's encodings; it's a ~5–15% off approximation for Claude but accurate enough for budget enforcement and avoids both an SDK dep and a network round-trip.

The token limit is enforced once, at `NodeStore.save`. Any node whose description exceeds `config.max_description_tokens` raises `DescriptionTooLong` before any file is written.

## Disk layout & `NodeStore`

### On-disk shape

```
<root>/
├── config.json
├── <node_id>/
│   ├── meta.json         # always present
│   ├── code.py           # CodeNodes only
│   └── tests.py          # CodeNodes only; optional but expected if `tests` is non-empty
└── build/                # regenerated; not authoritative
    ├── __init__.py
    ├── _manifest.json
    ├── <node_id>.py      # one per built CodeNode
    └── test_<node_id>.py # one per built CodeNode with tests
```

`<root>/build/` is owned by the `Builder`. The `NodeStore` never reads from or writes to it.

### `meta.json` schema

```json
{
  "node_id": "a1b2c3d4e5f6",
  "node_type": "code",
  "name": "rolling_mean",
  "description": "Streaming mean over a window.",
  "parent_id": "f7e6d5c4b3a2",
  "tags": [{"text": "stats", "v": [0.12, -0.04]}],
  "dependencies": ["e9d8c7b6a5f4"],
  "object_type": "method",
  "tests": [{"name": "handles_empty_input", "status": "unrun"}]
}
```

FolderNodes use the same envelope with `node_type: "folder"`, `children: [...]`, and no `dependencies`/`object_type`/`tests`. `np.ndarray` tag vectors serialize as plain lists.

### Public API

```python
class NodeStore:
    def __init__(self, config: LibraryConfig) -> None: ...
    def exists(self, node_id: NodeId) -> bool: ...
    def load(self, node_id: NodeId) -> Node: ...
    def load_code(self, node_id: NodeId) -> str: ...
    def load_tests(self, node_id: NodeId) -> str: ...           # may return "" if no tests.py
    def save(self, node: Node, code: str | None = None, tests: str | None = None) -> None: ...
    def delete(self, node_id: NodeId) -> None: ...
    def iter_ids(self) -> Iterator[NodeId]: ...
    def size_on_disk(self, node_id: NodeId) -> int: ...
    def node_dir(self, node_id: NodeId) -> Path: ...            # for the Builder
```

### Atomicity

`save` writes each of `meta.json.tmp`, `code.py.tmp`, `tests.py.tmp`, then `os.replace`s them into place. A crash mid-write leaves the previous good file intact; a missing `.tmp` is safely ignored on next read.

### Error mapping

- Missing node folder → `NodeNotFound`.
- Malformed/missing JSON fields → `CorruptMetaFile`.
- Description over budget → `DescriptionTooLong` (raised before any disk write).
- CodeNode name not a valid Python identifier → `InvalidNodeName` (raised before any disk write).
- Saving an existing id is allowed (overwrite). `Graph.add_node` is the one that raises `DuplicateNodeId`.

## `NodeCache` (LRU + TTL)

### Public API

```python
class NodeCache:
    def __init__(self, store: NodeStore, max_bytes: int, ttl_seconds: float) -> None: ...
    def get(self, node_id: NodeId) -> Node: ...
    def get_code(self, node_id: NodeId) -> str: ...
    def put(self, node: Node, code: str | None = None) -> None: ...
    def invalidate(self, node_id: NodeId) -> None: ...
    def clear(self) -> None: ...
    def stats(self) -> CacheStats: ...
```

`CacheStats` reports `hits`, `misses`, `evictions`, `current_bytes`, `entry_count`.

### Internals

- Backed by `collections.OrderedDict[NodeId, _CacheEntry]`. O(1) LRU via `move_to_end` on hit and `popitem(last=False)` on evict.
- `_CacheEntry` holds `node`, optional `code: str`, `size_bytes`, `last_access: float`.
- `size_bytes` = `len(json.dumps(meta))` + `len(code.encode())`. Approximate but stable across runs — good enough for budget enforcement.
- A running `_current_bytes` total is kept; never recomputed by scanning.

### Eviction flow on every `get`/`put`

1. **TTL pass.** If the touched entry's `now - last_access > ttl_seconds`, drop it. A `_sweep_expired()` helper is exposed for callers that want to do a full pass; no background thread.
2. **LRU pass.** While `_current_bytes > max_bytes`, pop the oldest entry.
3. On hit, `move_to_end` and refresh `last_access`.

### Other properties

- **Code caching is opt-in.** `get` returns metadata only. `get_code` separately loads and caches code text. This keeps a 100 KB code file from silently eating the cache budget when nobody asked for the code.
- **Write-through.** `put` writes via `store.save` first, then updates the cache. No dirty entries; a crash never loses data.
- **Single-threaded for v1.** No locks. Documented in the module docstring.

## `Builder`

The Builder materializes built CodeNodes into `<root>/build/` so they can be imported as `build.<node_id>`. It is incremental: a node is rebuilt only when its own code or any declared dependency's code has changed since the last build.

### Public API

```python
class Builder:
    def __init__(self, store: NodeStore, cache: NodeCache, build_root: Path) -> None: ...
    def ensure_built(self, node_id: NodeId) -> bool:
        """Build the node and its deps if needed. Returns True if anything was rebuilt."""
    def is_stale(self, node_id: NodeId) -> bool: ...
    def invalidate(self, node_id: NodeId) -> None:
        """Drop the manifest entry for this node (next ensure_built will rebuild)."""
    def clean(self) -> None:
        """Wipe <root>/build/ entirely; manifest is re-empty."""
```

### Manifest

`<root>/build/_manifest.json`:

```json
{
  "abc123def456": {
    "code_hash": "sha256:...",
    "dep_hashes": {"def456abcd12": "sha256:..."},
    "built_at": "2026-05-23T18:04:11Z"
  }
}
```

Hashes are SHA-256 of the raw `code.py` bytes (test code is not part of the build hash — changing tests doesn't trigger a rebuild, only a re-run).

### `ensure_built(node_id)` algorithm

`ensure_built(node_id)` is the public entry; the recursion uses a private `_ensure_built(node_id, visited)` that threads a `visited: dict[NodeId, str]` (id → code_hash) so diamond-shaped dep graphs don't re-walk shared subtrees. Each node is touched at most once per top-level call.

1. If `node_id` is already in `visited`, return its cached `code_hash` and skip further work.
2. Load the node and its `code.py` from the cache.
3. Compute `code_hash = sha256(code_text.encode())`. Record `visited[node_id] = code_hash`.
4. For each `dep_id` in the node's dependencies:
   - If the store has no such node → raise `MissingDependency`.
   - Recursively `_ensure_built(dep_id, visited)`.
   - Record the returned hash into `current_dep_hashes`.
5. Look up the manifest entry. If `code_hash` and `current_dep_hashes` both match the entry → skip; return `code_hash` (and propagate "no rebuild" to the public caller).
6. Otherwise rebuild **in this order**:
   a. Generate the dep-import preamble (see below) from the dep list.
   b. Write `<root>/build/<node_id>.py` = preamble + the node's `code.py` content (tmp + `os.replace`).
   c. If the node has a non-empty `tests.py`, write `<root>/build/test_<node_id>.py` = test preamble + `tests.py` content (tmp + `os.replace`).
   d. **Only after the build files are durably on disk**, update the in-memory manifest, then write `_manifest.json` via tmp + `os.replace`.
   e. Return `code_hash`.

**Write ordering matters.** Build files are written before the manifest is updated. A crash between (b/c) and (d) leaves orphan build files but no manifest claim — next `ensure_built` will rebuild them (wasted work, correct state). A crash with the opposite ordering would leave the manifest claiming "built" with no file behind it, which is a real correctness bug; this ordering forbids it.

The public `ensure_built` returns `True` if any node in the transitive closure was actually rebuilt, else `False`.

### Stale detection on dependent nodes

The Builder never proactively walks dependents. If node B is updated and A depends on B, A's manifest entry still has the *old* `dep_hashes[B]`. The next time anyone calls `ensure_built(A)`, step 4 sees the mismatch and rebuilds A. Pull-based invalidation, no eager fan-out.

### Generated imports — dependency graph is authoritative

The Builder owns inter-node wiring. The user's `code.py` contains **only behaviour**: stdlib/third-party imports if it wants, plus the function/class body. It does **not** write `from build.X import Y` for dependencies; those imports are generated by the Builder from the declared dep list.

**Convention:** each CodeNode exposes exactly one top-level symbol whose identifier equals `node.name`. So `node.name` must be a valid Python identifier (enforced at `NodeStore.save`).

**Generated preamble** for `build/<node_id>.py`:

```python
# AUTO-GENERATED IMPORTS — do not edit
from build.<dep1_id> import <dep1.name>
from build.<dep2_id> import <dep2.name>
# END AUTO-GENERATED IMPORTS

<contents of code.py>
```

**Forbidden in `code.py`:** any `from build.<...>` import. The Builder AST-scans `code.py` for such imports and raises `BuildError` if it finds one. This keeps the "deps live in the graph" rule enforceable — there's no way to import another node behind the graph's back.

**Name collisions among deps:** if two deps share `name`, the generated preamble would collide. `Builder.ensure_built` detects this before writing and raises `BuildError(node_id, "duplicate dep symbol: <name>")`. The user resolves by renaming one of the deps (which is a real refactor at the graph level, not a code-only fix).

The same preamble convention applies to `tests.py`: the Builder prepends imports for the node itself and all its dependencies, so tests can call them by bare name. `tests.py` is also forbidden from containing `from build.X` imports.

### Build root layout

`<root>/build/__init__.py` is created on first build and stays empty. Each built node lives at `<root>/build/<node_id>.py`. Test files live at `<root>/build/test_<node_id>.py` so pytest's default `test_*.py` collection picks them up.

`<root>/build/` is git-ignorable; deleting it never loses authoritative state.

## `Runner`

The Runner is a thin wrapper around `pytest` as a subprocess. It runs the tests for a single CodeNode and returns per-test results that the caller folds back into the node.

### Public API

```python
@dataclass
class TestResult:
    name: str
    status: TestStatus
    detail: str | None    # short failure message for FAILING, else None

class Runner:
    def __init__(self, build_root: Path, python: str | None = None) -> None: ...
    def run_tests(self, node_id: NodeId) -> list[TestResult]: ...
```

### Algorithm

1. Build target path: `<build_root>/test_<node_id>.py`. If it doesn't exist, return an empty list.
2. Pick a temp report path (e.g. `<build_root>/.last_report_<node_id>.json`).
3. `subprocess.run([python or sys.executable, "-m", "pytest", str(target), "-q", "--no-header", f"--json-report={report_path}", "--json-report-omit=streams,collectors,warnings,keywords"], capture_output=True, text=True, cwd=<root>)`. `cwd` is the store root so `import build.X` resolves.
4. Read the JSON report. For each test entry:
   - `outcome == "passed"` → `PASSING`, `detail=None`
   - `outcome in ("failed", "error")` → `FAILING`, `detail` = the first line of the report's `longrepr`
   - any test listed in the node's `tests` whose name doesn't appear in the report stays `UNRUN`
5. Delete the temp report file.
6. Return the list.

Subprocess isolation is a deliberate choice — a crashing test can't take down the host process. Parsing a structured JSON report (rather than `-q` stdout) is robust across pytest versions and quotes the failure detail without regex gymnastics.

**Test dependency:** the Runner requires `pytest-json-report` (a small, mature pytest plugin) to be installed alongside pytest. This is a project test-time dep, not a runtime dep of the library itself — only callers that use `Runner.run_tests` need it.

### Non-zero exit codes

`pytest` returns non-zero when any test fails. That's expected behavior, not a `BuildError`. The Runner only treats subprocess failures *other than* test failures (e.g., pytest not installed, import errors at collection time, or the JSON report file missing) as raised errors via `BuildError`.



### Public API

```python
class Graph:
    def __init__(
        self,
        store: NodeStore,
        cache: NodeCache,
        builder: Builder,
        runner: Runner,
        config: LibraryConfig,
    ) -> None: ...

    @classmethod
    def open(cls, root: Path, **config_overrides) -> "Graph":
        """Load LibraryConfig from root, apply overrides, persist, build store/cache/builder/runner, rebuild index."""

    # navigation — uses the in-memory index, no disk hit
    def children_of(self, node_id: NodeId) -> set[NodeId]: ...
    def parent_of(self, node_id: NodeId) -> NodeId | None: ...
    def find_by_tag(self, tag_text: str) -> set[NodeId]: ...
    def dependencies_of(self, node_id: NodeId) -> set[NodeId]: ...
    def dependents_of(self, node_id: NodeId) -> set[NodeId]: ...

    # node access — cache → store fallthrough
    def get(self, node_id: NodeId) -> Node: ...
    def get_code(self, node_id: NodeId) -> str: ...
    def get_tests(self, node_id: NodeId) -> str: ...

    # mutation — write-through plus index update
    def add_node(self, node: Node, code: str | None = None, tests: str | None = None) -> NodeId: ...
    def update_node(self, node: Node, code: str | None = None, tests: str | None = None) -> None: ...
    def remove_node(self, node_id: NodeId) -> None: ...

    # build + run
    def ensure_built(self, node_id: NodeId) -> bool: ...
    def run_tests(self, node_id: NodeId) -> list[TestResult]:
        """ensure_built(node_id), run pytest, fold results into Test.status, save, return results."""
```

### `run_tests` flow

1. `self.ensure_built(node_id)`.
2. `results = self.runner.run_tests(node_id)`.
3. For each test in the node's `tests`, find its result by `name` and update its `status`. Tests with no matching result keep their previous status (a test removed from `tests.py` but still listed will surface as a mismatch the caller can detect by comparing names).
4. Save the updated node via `update_node` (write-through to store, code/tests unchanged).
5. Return `results`.

### Mutation behaviour around builds

`update_node(node, code=…)` invalidates the node's build manifest entry whenever `code` is provided. `update_node(node, tests=…)` does **not** invalidate the build — tests are not part of `code_hash` — but the next `run_tests` call will materialize the new `test_<node_id>.py` because the Builder always rewrites the test file when it rebuilds, and `run_tests` always re-materializes the test file from `tests.py` before running (Builder rewrites it during `ensure_built` if the build is stale; otherwise `run_tests` updates only the test file in place).

### Index

Private to `Graph`. Built by walking `store.iter_ids()` at startup.

```python
@dataclass
class _Index:
    parent: dict[NodeId, NodeId | None]
    children: dict[NodeId, set[NodeId]]
    tags: dict[str, set[NodeId]]            # tag text → node ids
    dependents: dict[NodeId, set[NodeId]]   # reverse dependency edge
```

Index holds only IDs and tag text — no vectors, no code. Stays tiny even with thousands of nodes.

### Mutation contract

Every `add_node`/`update_node`/`remove_node` updates the index in lockstep with the store. `add_node` raises `DuplicateNodeId` if the id already exists. `remove_node` cascades through the index (removes the entry from its parent's `children`, from `tags`, and from any `dependents` lists).

## Error handling summary

Errors surface from the layer that detects them and propagate unwrapped:

- Disk corruption → `CorruptMetaFile` from `NodeStore.load`.
- Missing id → `NodeNotFound` from `NodeStore.load`, `load_code`, `delete`.
- Description over budget → `DescriptionTooLong` from `NodeStore.save` (and therefore from `Graph.add_node`/`update_node`).
- Duplicate id on add → `DuplicateNodeId` from `Graph.add_node`.

Callers catch the specific exception they care about; nothing is swallowed inside the library.

## Testing

`tests/library/` mirrors the source layout. All disk tests use pytest's `tmp_path` fixture so they run in an ephemeral directory and need no cleanup. Runner: `pytest`.

- **`test_nodes.py`** — dataclass equality, `is_built` is per-instance not per-class, `TestStatus` round-trips through JSON.
- **`test_ids.py`** — `new_node_id` returns 12-char hex; no collisions over a reasonable sample.
- **`test_config.py`** — `load` returns defaults when `config.json` missing; `save` round-trip; kwarg overrides on `Graph.open` get persisted.
- **`test_tokens.py`** — known strings produce known counts (within tiktoken's exactness for OpenAI encodings); encoder cache reused across calls.
- **`test_store.py`** — round-trip a FolderNode and a CodeNode (with `code.py` and `tests.py`); `NodeNotFound` on missing id; `CorruptMetaFile` on bad JSON; atomic-write survives a simulated crash (write tmp, kill, original untouched); `DescriptionTooLong` rejects save before any file appears; `InvalidNodeName` rejects a CodeNode whose name isn't a Python identifier.
- **`test_cache.py`** — hit/miss bookkeeping; LRU eviction when over `max_bytes`; TTL expiry drops stale entries; `put` writes through to store; `get` does not load code; `get_code` does and is then cached.
- **`test_builder.py`** — `ensure_built` writes `build/<id>.py` with the generated import preamble; second call is a no-op (`returns False`); updating a dep's code invalidates the dependent (next `ensure_built` returns `True` and rewrites the file); `MissingDependency` raised when a declared dep doesn't exist; `BuildError` raised when `code.py` contains a `from build.X` import; `BuildError` raised on duplicate dep symbol; `clean()` wipes `build/` and the manifest.
- **`test_runner.py`** — runs a passing test (status PASSING); runs a failing test (status FAILING with detail); runs a test that raises at collection time (raises `BuildError`); a node with no `tests.py` returns an empty list.
- **`test_graph.py`** — index rebuild from disk matches what was written; `find_by_tag` / `parent_of` / `dependencies_of` / `dependents_of`; `add → remove → add` round-trip leaves index clean; `add_node` raises on duplicate id; `run_tests` end-to-end on a tiny CodeNode that depends on another CodeNode (verifies the full pipeline: write deps → write parent → run_tests passes).

## Migration from the current file

The current `src/library/graph.py` is a skeleton with no callers. The cleanest path is to delete the file's contents and rewrite into the modules above. No backwards-compatibility shims needed.

## Future work (explicitly not in this spec)

- Higher-level search UX on top of `find_by_tag` and `iter_ids` (the "Claude browses the library" surface).
- **Vector tag similarity search.** v2 will populate `Tag.v` with word2vec-style embeddings of the tag text (model TBD — word2vec, fastText, or a small sentence-transformer). The Graph will gain a `find_by_tag_similar(query: str, top_k: int) -> list[NodeId]` that embeds the query and ranks tags by cosine similarity. Implications for v1: keep `Tag.v` persisted even though unused, and keep tag vectors out of the in-memory index (load lazily only when similarity search runs) so v1 startup stays fast.
- Concurrent access (locking the cache, file locks on the store, build manifest locking).
- Switching the store to SQLite if the folder-per-node layout becomes a bottleneck.
- Parallel rebuilds across independent subgraphs.
- In-process or long-lived pytest worker to skip the ~300–800 ms subprocess startup per `run_tests` call.
