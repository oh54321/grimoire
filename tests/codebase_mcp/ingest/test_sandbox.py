from pathlib import Path

import pytest

from codebase_mcp.ingest.sandbox import Sandbox, FetchError


def _src(tmp_path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "core.py").write_text("from mcp.server.fastmcp import FastMCP\n")
    (src / "util.py").write_text("def helper():\n    return 1\n")
    (src / "README.md").write_text("# hi\n")
    return src


def test_fetch_local_copies_and_flags_mcp(tmp_path):
    sb = Sandbox(tmp_path / "ingest")
    f = sb.fetch(str(_src(tmp_path)))
    assert (f.root / "core.py").exists()
    assert f.file_count == 2          # .py files only
    assert f.looks_like_mcp is True
    assert set(f.top_modules) == {"core", "util"}
    assert sb.path(f.session) == f.root


def test_fetch_missing_path_raises(tmp_path):
    sb = Sandbox(tmp_path / "ingest")
    with pytest.raises(FetchError):
        sb.fetch(str(tmp_path / "nope"))


def test_discard_removes_session(tmp_path):
    sb = Sandbox(tmp_path / "ingest")
    f = sb.fetch(str(_src(tmp_path)))
    assert sb.discard(f.session) is True
    assert not f.root.exists()
    assert sb.discard(f.session) is False   # already gone


def test_top_modules_excludes_init_and_includes_packages(tmp_path):
    src = tmp_path / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text("x = 1\n")
    (src / "pkg" / "__init__.py").write_text("")
    sb = Sandbox(tmp_path / "ingest")
    f = sb.fetch(str(src))
    assert "__init__" not in f.top_modules
    assert "main" in f.top_modules
    assert "pkg" in f.top_modules
