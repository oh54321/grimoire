import numpy as np
import pytest
from grimoire.api.search_system import SearchSystem
from tests.api.test_search_system import FakeEmbedder


class Counting(FakeEmbedder):
    def __init__(self):
        self.calls = 0

    def encode(self, phrase):
        self.calls += 1
        return super().encode(phrase)


def _ss(tmp_path, emb):
    ss = SearchSystem.open(tmp_path / "index", embedder=emb)
    for i in range(25):
        ss.index_node(f"n{i}", f"fn{i}", f"item {i}", "method", {"@kind:method", "@in:f1"})
    return ss


def test_paging_slices_and_counts(tmp_path):
    ss = _ss(tmp_path, FakeEmbedder())
    p0 = ss.search_page("item", page=0, page_size=10)
    assert len(p0.hits) == 10 and p0.total == 25 and p0.num_pages == 3
    p2 = ss.search_page("item", page=2, page_size=10)
    assert len(p2.hits) == 5


def test_page_flip_embeds_once(tmp_path):
    emb = Counting()
    ss = _ss(tmp_path, emb)
    emb.calls = 0
    ss.search_page("item", page=0, page_size=10)
    ss.search_page("item", page=1, page_size=10)
    ss.search_page("item", page=2, page_size=10)
    assert emb.calls == 1


def test_out_of_range_and_empty(tmp_path):
    ss = _ss(tmp_path, FakeEmbedder())
    with pytest.raises(IndexError):
        ss.search_page("item", page=99, page_size=10)
    empty = ss.search_page("item", page=0, page_size=10, tags={"nope"})
    assert empty.hits == [] and empty.num_pages == 0


def test_mutation_clears_cache(tmp_path):
    ss = _ss(tmp_path, FakeEmbedder())
    first = ss.search_page("item", page=0, page_size=10)
    assert first.total == 25
    ss.index_node("n99", "fn99", "item 99", "method", {"@kind:method", "@in:f1"})
    after = ss.search_page("item", page=0, page_size=10)
    assert after.total == 26          # cache was cleared → recomputed with the new node
