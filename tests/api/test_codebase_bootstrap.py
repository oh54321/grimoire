import pytest
from api.codebase import Codebase
from api.errors import ApiError
from tests.api.test_search_system import FakeEmbedder


def _open(tmp_path):
    return Codebase.open(tmp_path, embedder=FakeEmbedder())


def test_first_open_creates_single_root(tmp_path):
    cb = _open(tmp_path)
    rid = cb.root_id
    assert rid
    node = cb.load(rid)
    assert node.parent_id is None and node.name == "root"


def test_reopen_reuses_root(tmp_path):
    cb1 = _open(tmp_path)
    rid = cb1.root_id
    cb2 = _open(tmp_path)
    assert cb2.root_id == rid
