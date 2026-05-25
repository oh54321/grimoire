from grimoire.api.codebase import Codebase
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_rebuild_reports_unimplemented_as_incomplete_not_failed(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("f", "does f")          # abstraction only -> defined, never implemented
    report = cb.rebuild()
    # A never-implemented node is "incomplete" work-in-progress, not a build failure.
    assert nid in report.incomplete
    assert nid not in report.failed
    assert nid in cb.dirty()


def test_rebuild_after_implement_is_clean(tmp_path):
    cb = _open(tmp_path)
    nid = cb.add_method("inc", "add one")
    cb.implement(nid, "def inc(x):\n    return x + 1\n",
                 "def test_inc():\n    assert inc(1) == 2\n")
    assert nid not in cb.dirty()
    report = cb.rebuild()
    assert nid in report.skipped or report.passed == []   # nothing dirty to redo
