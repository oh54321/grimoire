from api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_rebuild_reports_failures_for_unimplemented(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("f", "does f")          # abstraction only -> dirty, unbuildable
    report = cb.rebuild()
    assert nid in report.failed
    assert nid in cb.dirty()
