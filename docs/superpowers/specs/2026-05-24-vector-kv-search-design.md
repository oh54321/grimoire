# Vector KV Search — Design

Date: 2026-05-24
Status: Approved, ready for implementation plan.

## Goal

Provide a small embedded vector key-value store under `src/search/` that lets callers add `(phrase, value)` pairs and retrieve the top-N values whose phrases are semantically closest to a query phrase. Designed for multi-threaded read-heavy use (cache-style access) with occasional writes.

## Public API

```python
from search import VectorConverter, KVDatabase

db = KVDatabase(path=Path("./cache.kvdb"))   # auto-loads if path exists
db.add("how do I reset my password", {"article_id": 42})
db.add("password reset steps", {"article_id": 42})   # different phrase, same value, fine
results = db.search("forgot password", n=5)
# -> [({"article_id": 42}, 0.81), ...]
db.save()
```

### `VectorConverter`

```python
class VectorConverter:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"): ...
    def encode(self, phrase: str) -> np.ndarray:
        """Return an L2-normalised float32 vector of shape (dim,)."""
    @property
    def dim(self) -> int: ...
```

- Lazy-loads the sentence-transformers model on first `encode`.
- Holds an internal `threading.Lock` around the model call — sentence-transformers is not safe under concurrent `encode`.
- Output is L2-normalised so cosine similarity reduces to a dot product.

### `KVDatabase`

```python
JSONValue = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]

class KVDatabase:
    def __init__(
        self,
        path: Path | None = None,
        embedder: VectorConverter | None = None,
        initial_capacity: int = 1024,
    ): ...

    def add(self, phrase: str, value: JSONValue) -> None: ...
    def search(self, phrase: str, n: int) -> list[tuple[JSONValue, float]]: ...
    def save(self) -> None: ...
    def load(self) -> None: ...
    def __len__(self) -> int: ...
    def __contains__(self, phrase: str) -> bool: ...
```

- Values are JSON-serialisable only (`dict` / `list` / `str` / `int` / `float` / `bool` / `None`). `add` validates by attempting `json.dumps(value)` and raising `TypeError` on failure.
- `path is None` → in-memory only; `save()` raises `RuntimeError`.
- `path` exists on disk → `__init__` auto-runs `load()`.
- `embedder is None` → constructs a default `VectorConverter()`.
- Overwrite semantics: `add(phrase, v2)` on an existing phrase replaces the previous value.
- `search` returns `(value, cosine_similarity)` pairs, highest similarity first, length ≤ min(n, len(db)).

## Internals

### Index

- hnswlib `Index(space="cosine", dim=embedder.dim)`.
- `init_index(max_elements=initial_capacity, ef_construction=200, M=16)`.
- `set_ef(50)` at query time.
- Auto-resize: when `len(index) == max_elements`, call `resize_index(max_elements * 2)` under the writer lock before the next add.

### In-memory state

```python
@dataclass
class _Store:
    phrase_to_id: dict[str, int]
    id_to_value: dict[int, JSONValue]     # int keys; serialised as strings in JSON
    id_to_phrase: dict[int, str]
    next_id: int
    dim: int
    model_name: str
```

`dim` and `model_name` are recorded so `load()` can refuse a mismatched embedder rather than silently returning garbage.

### Overwrite

hnswlib has no in-place replace. On overwriting `add`:

1. Look up `old_id = phrase_to_id[phrase]`.
2. `index.mark_deleted(old_id)`.
3. Allocate `new_id = next_id; next_id += 1`.
4. `index.add_items(vec, [new_id])`.
5. Update all three maps; drop `old_id` from `id_to_value` and `id_to_phrase`.

Soft deletes are tolerable for typical cache use. If the deleted-vs-live ratio becomes a problem later, a `compact()` method can rebuild the index — explicitly out of scope for v1.

### Concurrency

A custom `ReadWriteLock` lives in `kvdb.py`:

- Many concurrent readers allowed.
- Writers wait for all readers to drain, then hold the lock exclusively.
- Writer-preference (a waiting writer blocks new readers) to avoid writer starvation under heavy read load.
- Reentrant for the writer (so `add` can call internal helpers that also want the writer lock without deadlocking).

Lock usage:

| Method         | Lock        |
|----------------|-------------|
| `search`       | read lock   |
| `add`          | write lock  |
| `save`         | read lock   |
| `load`         | write lock  |
| `__len__`      | read lock   |
| `__contains__` | read lock   |

Inside `search`, we call `self._index.set_num_threads(1)` so multiple Python threads each run their own single-threaded query in parallel rather than contending over hnswlib's internal pool.

`VectorConverter.encode` is called *outside* the db's read/write lock (since the embedder has its own lock, and embedding is the expensive part — no reason to serialise the index against it).

### Async

No async API on the class. Documented idiom for asyncio callers:

```python
results = await asyncio.to_thread(db.search, phrase, n)
```

This keeps the surface area minimal and works with any event loop.

## Persistence

Layout under `path/` (a directory):

```
path/
  index.bin      # hnswlib serialised index (binary, not pickle)
  store.json    # _Store serialised as JSON
```

`store.json` shape:

```json
{
  "version": 1,
  "model_name": "all-MiniLM-L6-v2",
  "dim": 384,
  "next_id": 17,
  "phrase_to_id": {"hello world": 3, ...},
  "id_to_phrase": {"3": "hello world", ...},
  "id_to_value":  {"3": {"article_id": 42}, ...}
}
```

Integer keys are stringified in JSON and parsed back to `int` on load (standard JSON limitation).

- `save()` writes both files atomically: write to `*.tmp`, then `os.replace`.
- `load()` validates `version == 1`, `dim == embedder.dim`, `model_name == embedder.model_name`; raises `ValueError` otherwise.

No pickle anywhere — values must be JSON-serialisable, which `add` enforces at insert time so a corrupt blob can't reach disk.

## Dependencies

Added to `pyproject.toml` `[project].dependencies`:

- `sentence-transformers>=2.7`
- `hnswlib>=0.8`

(`numpy` is already present.)

## File layout

```
src/search/
  __init__.py          # re-exports VectorConverter, KVDatabase
  embedder.py          # VectorConverter
  kvdb.py              # KVDatabase + ReadWriteLock + _Store
tests/search/
  __init__.py
  conftest.py          # session-scoped real-embedder fixture + fake-embedder fixture
  test_embedder.py
  test_kvdb.py
  test_kvdb_concurrency.py
```

## Testing

### `test_embedder.py`

- `encode("hello")` returns shape `(384,)`, dtype `float32`, L2 norm ≈ 1.0 (atol 1e-5).
- Same input → identical output (deterministic).
- Different inputs → different vectors.
- Skips with a clear message if the model cannot be loaded (offline / no weights cached).

### `test_kvdb.py`

Uses a `FakeEmbedder` (hash-derived deterministic vectors) so the suite stays fast and offline-safe.

- Add → search round-trip: the exact phrase added comes back as the top hit with similarity ≈ 1.0.
- Top-N ordering: closer phrases rank higher than far ones.
- Overwrite: `add("p", v1); add("p", v2); search("p", 1)` returns `v2`, and `len(db) == 1`.
- `__contains__` and `__len__` reflect adds and overwrites correctly.
- Empty db: `search("anything", 5) == []`.
- `n` larger than db size returns all entries.
- `add` with a non-JSON-serialisable value (e.g. a `set` or custom object) raises `TypeError`.
- Save → load round-trip on disk reproduces the same search results.
- `load()` with mismatched `dim` or `model_name` raises `ValueError`.
- Resize: add 1025 items with `initial_capacity=1024`, confirm no crash and all searchable.

### `test_kvdb_concurrency.py`

- Seed the db with ~200 phrases.
- Spawn 8 reader threads each doing 500 random searches and one writer thread doing 100 overwriting adds.
- Assertions:
  - No exception raised in any thread.
  - Every returned value was, at some point, added to the db (i.e. no torn reads producing values from neither the old nor new state).
  - Final `len(db)` matches the number of distinct phrases ever added.

## Out of scope (v1)

- `add_many` / batched embedding.
- `delete(phrase)` (we have implicit delete via overwrite; explicit delete can come later).
- `compact()` to reclaim soft-deleted ids.
- Multi-process safety (file locking). Single-process multi-threaded only.
- Non-cosine spaces.
