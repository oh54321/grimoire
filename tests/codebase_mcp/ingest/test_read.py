from pathlib import Path

import pytest

from codebase_mcp.ingest.survey import read_symbol


def _mod(tmp_path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    (root / "m.py").write_text(
        "def a():\n    return 1\n\n"
        "def b():\n    return 2\n"
    )
    return root


def test_read_whole_file(tmp_path):
    root = _mod(tmp_path)
    text = read_symbol(root, "m.py")
    assert "def a()" in text and "def b()" in text


def test_read_single_symbol(tmp_path):
    root = _mod(tmp_path)
    text = read_symbol(root, "m.py", "b")
    assert text.strip().startswith("def b():")
    assert "def a()" not in text


def test_read_missing_symbol_raises(tmp_path):
    root = _mod(tmp_path)
    with pytest.raises(KeyError):
        read_symbol(root, "m.py", "nope")
