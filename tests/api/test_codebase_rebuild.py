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


def test_rebuild_after_implement_is_clean(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_inc():\n    assert inc(1) == 2\n")
    assert nid not in cb.dirty()
    report = cb.rebuild()
    assert nid in report.skipped or report.passed == []   # nothing dirty to redo
