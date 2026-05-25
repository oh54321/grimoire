import numpy as np
import pytest
from api.search_system import SearchSystem


class FakeEmbedder:
    model_name = "fake"
    dim = 8

    def encode(self, phrase: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(phrase)) % (2**32))
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-12)


def _ss(tmp_path):
    return SearchSystem.open(tmp_path / "index", embedder=FakeEmbedder())


def test_index_search_and_remove(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "rolling_mean", "streaming mean", "method",
                  {"stats", "@kind:method", "@in:f1"})
    ss.index_node("n2", "RingBuffer", "circular buffer", "class",
                  {"@kind:class", "@in:f1"})
    hits = ss.search("mean", n=10, tags={"stats"})
    assert [h.node_id for h in hits] == ["n1"]
    ss.remove_node("n1")
    assert ss.search("mean", n=10, tags={"stats"}) == []


def test_duplicate_descriptions_both_indexed(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "same text", "method", {"@kind:method", "@in:f1"})
    ss.index_node("n2", "b", "same text", "method", {"@kind:method", "@in:f2"})
    f1 = {h.node_id for h in ss.search("same", n=10, folders={"f1"})}
    f2 = {h.node_id for h in ss.search("same", n=10, folders={"f2"})}
    assert f1 == {"n1"} and f2 == {"n2"}


def test_folder_and_type_or_semantics(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "d", "method", {"@kind:method", "@in:f1"})
    ss.index_node("n2", "b", "d", "class", {"@kind:class", "@in:f2"})
    ss.index_node("n3", "c", "d", "method", {"@kind:method", "@in:f3"})
    got = {h.node_id for h in ss.search("d", n=10, folders={"f1", "f2"})}
    assert got == {"n1", "n2"}
    got = {h.node_id for h in ss.search("d", n=10, object_types={"class", "method"}, folders={"f1", "f2"})}
    assert got == {"n1", "n2"}


def test_list_tags_excludes_synthetics(tmp_path):
    ss = _ss(tmp_path)
    ss.index_node("n1", "a", "d", "method", {"stats", "@kind:method", "@in:f1"})
    assert ss.list_tags() == {"stats"}
