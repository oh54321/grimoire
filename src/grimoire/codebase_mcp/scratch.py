from __future__ import annotations

import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from grimoire.library.runner import _worker_env


@dataclass(frozen=True)
class ScratchResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class ScratchRunner:
    """Runs throwaway Python with the codebase's build/ importable. Persists nothing."""

    def __init__(self, root: Path, timeout: float = 30.0, python: str | None = None) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._python = python or sys.executable

    def run(self, code: str, import_lines: tuple[str, ...] = ()) -> ScratchResult:
        body = ("\n".join(import_lines) + "\n\n" + code) if import_lines else code
        path = self._root / f"_scratch_{uuid.uuid4().hex}.py"
        path.write_text(body)
        try:
            proc = subprocess.run(
                [self._python, str(path)],
                cwd=str(self._root),
                env=_worker_env(self._root),
                capture_output=True, text=True, timeout=self._timeout,
            )
            return ScratchResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return ScratchResult(None, out, err, True)
        finally:
            path.unlink(missing_ok=True)
