from pathlib import Path

import pytest

from grimoire.codebase_mcp.ingest.survey import read_symbol


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


def test_read_class_method(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "c.py").write_text(
        "class Client:\n"
        "    def send(self, msg):\n"
        "        return msg\n"
    )
    text = read_symbol(root, "c.py", "Client.send")
    assert text.strip().startswith("def send(self, msg):")
    assert "class Client" not in text


def test_read_symbol_rejects_parent_traversal(tmp_path):
    root = _mod(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_symbol(root, "../../../../etc/passwd")


def test_read_symbol_rejects_absolute_path(tmp_path):
    root = _mod(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_symbol(root, "/etc/hosts")
