"""Warm pytest worker. Reads one JSON request per line on stdin
({"target": "<test_file>", "report": "<json_report_path>"}), runs pytest
in-process against the target writing a json report, and replies with one
JSON line. Run as: python -m library._test_worker <store_root>
"""
import io
import json
import sys


def _evict_build_modules() -> None:
    for name in list(sys.modules):
        if name == "build" or name.startswith("build.") or name.startswith("test_"):
            del sys.modules[name]


def main() -> None:
    store_root = sys.argv[1]
    if store_root not in sys.path:
        sys.path.insert(0, store_root)
    import pytest  # imported once; stays warm

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        _evict_build_modules()
        # Redirect pytest stdout/stderr to devnull so only our sentinel
        # JSON line appears on the worker's stdout.
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            pytest.main([
                req["target"], "-q", "--no-header", "-p", "no:cacheprovider",
                "--json-report", f"--json-report-file={req['report']}",
                "--json-report-omit=streams,warnings,keywords",
            ])
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        sys.stdout.write(json.dumps({"done": True}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
