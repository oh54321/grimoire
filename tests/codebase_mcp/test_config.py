from pathlib import Path
from codebase_mcp.config import McpConfig


def test_defaults_when_env_empty():
    cfg = McpConfig.from_env(env={})
    assert cfg.root == Path.home() / ".grimoire" / "codebase"
    assert cfg.min_tests == 3
    assert cfg.max_folder_children == 7
    assert cfg.scratch_timeout == 30.0


def test_env_overrides():
    env = {
        "GRIMOIRE_CODEBASE": "/tmp/cb",
        "GRIMOIRE_MIN_TESTS": "5",
        "GRIMOIRE_MAX_FOLDER_CHILDREN": "10",
        "GRIMOIRE_SCRATCH_TIMEOUT": "12.5",
    }
    cfg = McpConfig.from_env(env=env)
    assert cfg.root == Path("/tmp/cb")
    assert cfg.min_tests == 5
    assert cfg.max_folder_children == 10
    assert cfg.scratch_timeout == 12.5
