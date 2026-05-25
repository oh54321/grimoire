from __future__ import annotations

import shutil
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
        else:
            raise FetchError(f"local path not found: {source}")
        return self._describe(session, dest)

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
