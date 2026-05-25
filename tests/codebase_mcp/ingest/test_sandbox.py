import subprocess
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


def _git_repo(tmp_path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 2\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True,
                                    capture_output=True)
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return f"file://{repo}"


def test_fetch_git_url_clones(tmp_path):
    sb = Sandbox(tmp_path / "ingest")
    f = sb.fetch(_git_repo(tmp_path))
    assert (f.root / "mod.py").exists()
    assert not (f.root / ".git").exists()   # .git stripped after clone
    assert f.file_count == 1


def test_fetch_bad_url_raises(tmp_path):
    sb = Sandbox(tmp_path / "ingest", timeout=10.0)
    with pytest.raises(FetchError):
        sb.fetch("file:///definitely/not/a/repo")


def test_fetch_git_url_with_ref(tmp_path):
    repo = tmp_path / "repo_ref"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True,
                                    capture_output=True)
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    run("branch", "feature")
    sb = Sandbox(tmp_path / "ingest")
    f = sb.fetch(f"file://{repo}", ref="feature")
    assert (f.root / "a.py").exists()


def test_fetch_git_url_bad_ref_raises(tmp_path):
    repo = tmp_path / "repo_badref"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True,
                                    capture_output=True)
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    sb = Sandbox(tmp_path / "ingest")
    with pytest.raises(FetchError):
        sb.fetch(f"file://{repo}", ref="no_such_branch")
