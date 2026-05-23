import json
from pathlib import Path

from library.config import LibraryConfig


def test_load_returns_defaults_when_file_missing(tmp_path: Path):
    cfg = LibraryConfig.load(tmp_path)
    assert cfg.root_path == tmp_path
    assert cfg.max_cache_mb == 50
    assert cfg.ttl_seconds == 3600.0
    assert cfg.max_description_tokens == 200
    assert cfg.tokenizer_encoding == "cl100k_base"


def test_save_then_load_roundtrip(tmp_path: Path):
    cfg = LibraryConfig(
        root_path=tmp_path,
        max_cache_mb=10,
        ttl_seconds=60.0,
        max_description_tokens=50,
        tokenizer_encoding="cl100k_base",
    )
    cfg.save()
    loaded = LibraryConfig.load(tmp_path)
    assert loaded == cfg


def test_save_writes_to_config_json(tmp_path: Path):
    cfg = LibraryConfig(root_path=tmp_path, max_cache_mb=7)
    cfg.save()
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["max_cache_mb"] == 7
    # root_path is not persisted — it's the directory the file lives in.
    assert "root_path" not in on_disk


def test_load_ignores_unknown_keys(tmp_path: Path):
    (tmp_path / "config.json").write_text(json.dumps({"max_cache_mb": 5, "future_key": "ignored"}))
    cfg = LibraryConfig.load(tmp_path)
    assert cfg.max_cache_mb == 5
