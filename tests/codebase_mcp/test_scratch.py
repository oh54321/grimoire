from pathlib import Path
from codebase_mcp.scratch import ScratchRunner


def _runner(tmp_path, timeout=10.0):
    (tmp_path / "build").mkdir(parents=True, exist_ok=True)
    return ScratchRunner(tmp_path, timeout=timeout)


def test_run_captures_stdout_and_zero_exit(tmp_path):
    r = _runner(tmp_path).run("print('hello scratch')")
    assert r.exit_code == 0
    assert not r.timed_out
    assert "hello scratch" in r.stdout


def test_nonzero_exit_is_reported(tmp_path):
    r = _runner(tmp_path).run("raise SystemExit(3)")
    assert r.exit_code == 3
    assert not r.timed_out


def test_timeout_is_killed(tmp_path):
    r = _runner(tmp_path, timeout=0.5).run("import time\ntime.sleep(5)\n")
    assert r.timed_out is True


def test_temp_file_is_cleaned_up(tmp_path):
    _runner(tmp_path).run("print('x')")
    leftovers = list((tmp_path).glob("_scratch_*.py"))
    assert leftovers == []


def test_import_lines_are_prepended(tmp_path):
    r = _runner(tmp_path)
    res = r.run("print(VALUE)", import_lines=("VALUE = 42",))
    assert res.exit_code == 0
    assert "42" in res.stdout
