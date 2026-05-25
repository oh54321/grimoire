from grimoire.api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_discover_then_filter_loop(tmp_path):
    cb = _open(tmp_path)
    io = cb.make_folder("io")
    m = cb.add_method("read_csv", "read a csv file", parent_id=io, tags=["parsing"])
    cb.add_class("Buffer", "a buffer", tags=["memory"])
    page = cb.search("csv", tags={"parsing"}, folders={io}, object_types={"method"})
    assert [h.node_id for h in page.hits] == [m]
    assert "read_csv" in page.render()
    tags_page = cb.search_tags("parse text")
    assert any(h.tag == "parsing" for h in tags_page.hits)
