from pathlib import Path

from grimoire.codebase_mcp.ingest.survey import survey


def _tree(tmp_path) -> Path:
    root = tmp_path / "src"
    root.mkdir()
    (root / "api.py").write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "app = FastMCP('x')\n\n"
        "@app.tool()\n"
        "def ping(host: str) -> bool:\n"
        '    "Ping a host."\n'
        "    return True\n\n"
        "def _secret(x):\n"
        "    return x\n\n"
        "class Client:\n"
        "    def send(self, msg: str) -> None:\n"
        '        "Send a message."\n'
        "        pass\n"
    )
    (root / "notes.txt").write_text("not python\n")
    return root


def test_survey_extracts_symbols(tmp_path):
    symbols, skipped = survey(_tree(tmp_path))
    by_q = {s.qualname: s for s in symbols}

    assert by_q["ping"].kind == "function"
    assert by_q["ping"].signature == "def ping(host: str) -> bool"
    assert by_q["ping"].doc_first_line == "Ping a host."
    assert by_q["ping"].mcp_tool is True

    assert by_q["_secret"].mcp_tool is False
    assert by_q["Client"].kind == "class"
    assert by_q["Client.send"].kind == "method"
    assert by_q["Client.send"].signature == "def send(self, msg: str) -> None"

    assert any(s.endswith("notes.txt") for s in skipped)


def test_survey_skips_unparseable_py(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "broken.py").write_text("def (")   # SyntaxError
    symbols, skipped = survey(root)
    assert symbols == []
    assert any(s.endswith("broken.py") for s in skipped)
