from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class McpConfig:
    root: Path
    min_tests: int = 3
    max_folder_children: int = 7
    scratch_timeout: float = 30.0

    @classmethod
    def from_env(cls, env: dict | None = None) -> "McpConfig":
        env = os.environ if env is None else env
        raw_root = env.get("GRIMOIRE_CODEBASE")
        root = Path(raw_root).expanduser() if raw_root else Path.home() / ".grimoire" / "codebase"

        def _int(name: str, default: int) -> int:
            v = env.get(name)
            return int(v) if v else default

        timeout = env.get("GRIMOIRE_SCRATCH_TIMEOUT")
        return cls(
            root=root,
            min_tests=_int("GRIMOIRE_MIN_TESTS", 3),
            max_folder_children=_int("GRIMOIRE_MAX_FOLDER_CHILDREN", 7),
            scratch_timeout=float(timeout) if timeout else 30.0,
        )
