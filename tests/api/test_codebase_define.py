from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_define_abstraction_is_searchable_and_dirty(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("rolling_mean", "streaming mean over a window", tags=["stats"])
    assert nid in cb.dirty()                         # no code yet
    assert cb.load_code(nid) == ""
    hits = cb.search("mean", tags={"stats"}, object_types={"method"}).hits
    assert any(h.node_id == nid for h in hits)
    assert "stats" in cb.list_tags()


def test_add_class_and_executable_kinds(tmp_path):
    cb = _open(tmp_path)
    c = cb.add_class("RingBuffer", "circular buffer")
    e = cb.add_executable("main", "entrypoint")
    assert cb.load(c).object_type == "class"
    assert cb.load(e).object_type == "executable"
