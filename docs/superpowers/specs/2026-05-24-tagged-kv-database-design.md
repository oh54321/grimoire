# Tagged KV Database — Design

Date: 2026-05-24
Status: Approved (pending implementation)

## Goal

Add a `TaggedKVDatabase` to `src/search/` that behaves like the existing
`KVDatabase` (phrase → value, with vector similarity search) but also attaches
a set of **tags** to each entry. Searches can:

- restrict vector search to entries that have **all** of a given set of tags
  (AND semantics), or
- list every entry matching a tag filter, no vector query at all.

Efficiency is a first-class requirement: filtered vector search must remain
fast across both restrictive ("only 50 entries match the tags") and permissive
("100k entries match") filters.

## Non-goals

- OR / NOT / arbitrary boolean tag expressions. AND-only.
- Editing tags after `add()`. Tags are immutable per entry; re-adding the
  phrase replaces its value *and* tags atomically.
- Adding a new third-party dependency. Pure-Python `set` is sufficient for the
  intersection workloads we target.
- Changing `KVDatabase`'s public API or persistence format.

## Architecture

### Shared base class

Extract a `_VectorStoreBase` into `src/search/_base.py` that owns everything
both classes already share:

- the `ReadWriteLock`, `_path`, `_embedder`, `_capacity`
- the HNSW index lifecycle (`init_index`, resize, `save_index`, `load_index`)
- the shared `_Store` fields: `phrase_to_id`, `id_to_value`, `id_to_phrase`,
  `next_id`, `dim`, `model_name`
- common helpers: `_encode(phrase)`, `_grow_capacity(new_id)`, and a
  persistence skeleton with two extension hooks:
  - `_extra_save_data() -> dict` — subclass payload to merge into `store.json`
  - `_extra_load_data(data: dict) -> None` — subclass rehydration from same

`KVDatabase` becomes a thin subclass that returns `{}` from both hooks and
otherwise behaves exactly as today. `TaggedKVDatabase` is a sibling subclass.

Rationale for subclassing over composition: persistence is a single atomic
`save()`/`load()` and the lock + index lifecycle are identical. Composition
would force lockstep management of two stores on disk.

### `TaggedKVDatabase` state

In addition to the base store:

- `id_to_tags: dict[int, frozenset[str]]` — canonical per-entry tag set.
- `tag_to_ids: dict[str, set[int]]` — inverted index for fast intersection.

`tag_to_ids` is **derived** from `id_to_tags` and rebuilt on `load()`. Only
`id_to_tags` is written to disk. This eliminates the possibility of on-disk
divergence between the two views.

Tags are non-empty strings. An entry may have zero tags (it will never match
any non-empty include filter).

## Public API

Module path: `src/search/tagged_kvdb.py`. Re-exported from
`src/search/__init__.py` as `TaggedKVDatabase`.

```python
class TaggedKVDatabase(_VectorStoreBase):
    def __init__(
        self,
        path: Path | None = None,
        embedder=None,
        initial_capacity: int = 1024,
        brute_force_threshold: int = 1000,
    ) -> None: ...

    def add(
        self,
        phrase: str,
        value: JSONValue,
        tags: Iterable[str] = (),
    ) -> None: ...

    def search(
        self,
        phrase: str,
        n: int,
        tags: Iterable[str] = (),
    ) -> list[tuple[JSONValue, float]]: ...

    def search_paged(
        self,
        phrase: str,
        page_size: int,
        max_pages: int | None = None,
        tags: Iterable[str] = (),
    ) -> PagedList[tuple[JSONValue, float]]: ...

    def list_by_tags(
        self,
        tags: Iterable[str],
    ) -> list[JSONValue]: ...

    def list_by_tags_paged(
        self,
        tags: Iterable[str],
        page_size: int,
        max_pages: int | None = None,
    ) -> PagedList[JSONValue]: ...

    def tags_of(self, phrase: str) -> frozenset[str]: ...
    def all_tags(self) -> set[str]: ...
```

Semantics:

- `tags=()` in any search/list method means "no tag filter".
- `tags` with values is treated as an AND filter: every returned entry must
  carry all the given tags.
- If any tag in the filter is unknown to the store, the result is `[]` — this
  is the natural set-intersection outcome and is not an error.
- `add(phrase, value, tags)` replaces an existing phrase entry's value *and*
  tags atomically. The old id is removed from each of its tag's `tag_to_ids`
  buckets, and any bucket that empties is pruned from the dict.
- `tags_of(phrase)` raises `KeyError` if the phrase is unknown (dict-like).

## Filtered search algorithm

The interesting work is in `search()`. Approach C from brainstorming —
adaptive between exact brute-force and HNSW with a filter callback.

Pseudocode, under the read lock:

```
vec = self._encode(phrase)   # done outside the lock if possible

allowed = self._intersect_tag_ids(tags)
  # empty tags → allowed is None (sentinel for "no filter")
  # any tag unknown → return []
  # sort per-tag sets by size ascending, then set.intersection(*sets)
  # if allowed is empty → return []

live = len(allowed) if allowed is not None else len(self._store.id_to_value)
k = min(n, live)
if k == 0: return []

if allowed is not None and len(allowed) <= self._brute_force_threshold:
    ids = list(allowed)
    mat = self._index.get_items(ids)         # (m, dim) float32
    # cosine similarity (vectors from hnswlib cosine space are stored raw;
    # normalise both sides)
    sims = _cosine_sim(mat, vec)             # in [-1, 1]
    if k < len(ids):
        top = np.argpartition(-sims, k - 1)[:k]
    else:
        top = np.arange(len(ids))
    top = top[np.argsort(-sims[top])]
    return [(self._store.id_to_value[ids[i]], float(sims[i])) for i in top]
else:
    filt = None if allowed is None else (lambda i: i in allowed)
    labels, distances = self._index.knn_query(
        vec.reshape(1, -1), k=k, filter=filt
    )
    # same result-assembly loop as KVDatabase.search today
```

Notes:

- **Why sort sets before intersecting:** Python's `set.intersection` iterates
  the smallest set when it's the receiver of the call, so starting from the
  smallest is meaningfully faster on skewed tag distributions.
- **Why brute-force at small `|allowed|`:** HNSW with a very restrictive
  filter callback can wander a long way before finding `k` matches; exact
  cosine on a tiny subset is both faster and exact.
- **Threshold:** module-level default `BRUTE_FORCE_THRESHOLD = 1000`,
  overridable per-instance via the constructor. Picked as a reasonable middle
  point; tuning is left to follow-up benchmarks if needed.
- **`list_by_tags()`** uses the same `_intersect_tag_ids()` helper and just
  materialises `[id_to_value[i] for i in sorted(allowed)]`. Sorted by id
  (== insertion order) so output is stable across calls.

## Concurrency

Same model as `KVDatabase` today. Every read path takes `_lock.read()`; every
mutation takes `_lock.write()`. `add()` updates `phrase_to_id`, `id_to_value`,
`id_to_phrase`, `id_to_tags`, and `tag_to_ids` inside one critical section so
external readers never observe a half-updated state.

The brute-force path calls `self._index.get_items(ids)` while holding the
read lock — hnswlib's reads are safe to interleave with our higher-level
read/write discipline.

## Persistence

`store.json` schema gains two new keys *only* when written by
`TaggedKVDatabase`:

```json
{
  "version": 2,
  "...": "all existing v1 fields unchanged",
  "id_to_tags": {"<id>": ["tag1", "tag2"], ...}
}
```

Rules:

- Plain `KVDatabase` keeps writing `version: 1` and the existing schema.
- `TaggedKVDatabase` writes `version: 2`. Loading a v1 file into
  `TaggedKVDatabase` is rejected with a clear error (the user should construct
  a `KVDatabase` instead, or migrate).
- Loading a v2 file into plain `KVDatabase` is also rejected.
- `tag_to_ids` is rebuilt from `id_to_tags` on load; never serialised.
- File layout, atomic write strategy (`*.tmp` + `os.replace`), and on-disk
  paths (`store.json`, `index.bin`) are unchanged.

## Error handling

- `add()`: existing JSON-serialisability check is preserved. `tags` must be an
  iterable of non-empty strings; anything else raises `TypeError` *before* any
  state is mutated.
- `search()` / `search_paged()` / `list_by_tags*()`: unknown tags → `[]`.
  Non-string tags → `TypeError`.
- `tags_of(phrase)`: `KeyError` if phrase not present.
- `__init__`: `brute_force_threshold` must be `>= 0`; `0` disables the
  brute-force path (always use HNSW filter).

## Testing

New file `tests/search/test_tagged_kvdb.py`:

- `add` + `search` with no tag filter behaves identically to `KVDatabase` on
  the same inputs.
- AND filter narrows results to entries that carry every listed tag.
- Unknown tag in filter returns `[]`.
- Re-adding a phrase updates both its value and its tag membership; the
  previous tag buckets no longer reference the old id; empty buckets are
  pruned from `tag_to_ids`.
- Brute-force vs HNSW path: build a small store, force
  `brute_force_threshold=0` for one run and a large value for another, and
  assert the top-k results match for the same query.
- `list_by_tags` and `list_by_tags_paged` return the full filtered set in id
  order, paged correctly.
- Save/load round-trip preserves `id_to_tags` exactly and rebuilds
  `tag_to_ids` correctly.
- Version-mismatch errors fire when crossing v1/v2 boundaries.
- Smoke concurrency test: several reader threads issuing filtered searches
  while one writer adds new entries — no exceptions, no stale tag references.

Plus: existing `KVDatabase` test suite must still pass after the base-class
extraction, confirming the refactor is behaviour-preserving.
