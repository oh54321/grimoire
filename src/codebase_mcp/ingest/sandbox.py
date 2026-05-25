from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

_MCP_MARKERS = ("import mcp", "from mcp", "fastmcp", "FastMCP")
_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")


class FetchError(Exception):
    """A source could not be fetched (missing path, clone failure, timeout)."""


@dataclass(frozen=True)
class Fetched:
    session: str
    root: Path
    file_count: int        # number of .py files
    looks_like_mcp: bool
    top_modules: tuple[str, ...]


class Sandbox:
    """Fetches a source into an ephemeral session dir. Nothing here is ever
    placed on an import path; cloned code is read-only browse material."""

    def __init__(self, ingest_root: Path, timeout: float = 60.0) -> None:
        self._root = Path(ingest_root)
        self._timeout = timeout

    def path(self, session: str) -> Path:
        return self._root / session

    def fetch(self, source: str, ref: str | None = None) -> Fetched:
        session = uuid.uuid4().hex[:12]
        dest = self.path(session)
        local = Path(source).expanduser()
        if local.exists():
            self._root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(local, dest, ignore=_IGNORE)
        elif self._is_url(source):
            self._clone(source, ref, dest)
        else:
            raise FetchError(f"not a local path or recognized git URL: {source}")
        return self._describe(session, dest)

    @staticmethod
    def _is_url(source: str) -> bool:
        return "://" in source or source.endswith(".git") or "@" in source

    def _clone(self, source: str, ref: str | None, dest: Path) -> None:
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [source, str(dest)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self._timeout)
        except subprocess.TimeoutExpired as e:
            raise FetchError(f"clone timed out after {self._timeout}s") from e
        if proc.returncode != 0:
            raise FetchError(f"clone failed: {proc.stderr.strip()[:300]}")
        shutil.rmtree(dest / ".git", ignore_errors=True)

    def discard(self, session: str) -> bool:
        dest = self.path(session)
        if dest.exists():
            shutil.rmtree(dest)
            return True
        return False

    def _describe(self, session: str, dest: Path) -> Fetched:
        py = sorted(dest.rglob("*.py"))
        looks_mcp = any(self._has_marker(p) for p in py)
        top = sorted(({p.stem for p in dest.glob("*.py")} - {"__init__"})
                     | {p.parent.name for p in dest.glob("*/__init__.py")})
        return Fetched(session, dest, len(py), looks_mcp, tuple(top))

    @staticmethod
    def _has_marker(p: Path) -> bool:
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            return False
        return any(m in text for m in _MCP_MARKERS)
