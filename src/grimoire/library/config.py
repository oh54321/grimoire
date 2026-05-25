"""Library-wide configuration, persisted per store at <root>/config.json."""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class LibraryConfig:
    root_path: Path
    max_cache_mb: int = 50
    ttl_seconds: float = 3600.0
    max_description_tokens: int = 200
    tokenizer_encoding: str = "cl100k_base"
    use_test_worker: bool = True
    test_timeout_seconds: float = 60.0
    min_tests_per_method: int = 0
    max_folder_children: int = 0

    @classmethod
    def load(cls, root: Path) -> "LibraryConfig":
        """Read root/config.json. Fall back to defaults if missing or empty."""
        path = root / "config.json"
        if not path.exists():
            return cls(root_path=root)
        data = json.loads(path.read_text())
        known = {f.name for f in fields(cls)} - {"root_path"}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(root_path=root, **kwargs)

    def save(self) -> None:
        """Write self to <root_path>/config.json. root_path itself is not persisted."""
        self.root_path.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("root_path", None)
        path = self.root_path / "config.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True))
