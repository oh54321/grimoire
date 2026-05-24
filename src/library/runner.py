"""Runs pytest as a subprocess against materialized build files, parses JSON report."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from library.errors import BuildError
from library.ids import NodeId
from library.nodes import TestStatus


@dataclass
class TestResult:
    __test__ = False  # tell pytest not to collect this dataclass as a test class

    name: str
    status: TestStatus
    detail: str | None


class Runner:
    def __init__(self, build_root: Path, python: str | None = None) -> None:
        self.build_root = build_root
        self.python = python or sys.executable

    def run_tests(self, node_id: NodeId) -> list[TestResult]:
        target = self.build_root / f"test_{node_id}.py"
        if not target.exists():
            return []

        report_path = self.build_root / f".last_report_{node_id}.json"
        if report_path.exists():
            report_path.unlink()

        argv = [
            self.python,
            "-m",
            "pytest",
            str(target),
            "-q",
            "--no-header",
            "--json-report",
            f"--json-report-file={report_path}",
            "--json-report-omit=streams,warnings,keywords",
        ]
        cwd = self.build_root.parent  # the store root, so `import build.X` resolves
        env = dict(os.environ)
        # Ensure `build.X` is importable regardless of pytest's rootdir heuristics.
        env["PYTHONPATH"] = str(cwd) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(cwd), env=env)

        if not report_path.exists():
            raise BuildError(
                node_id,
                f"pytest produced no JSON report (exit={proc.returncode}). stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )

        try:
            report = json.loads(report_path.read_text())
        finally:
            report_path.unlink(missing_ok=True)

        # Check for collection errors — these are top-level failures, not test failures.
        collectors = report.get("collectors", [])
        for c in collectors:
            if c.get("outcome") == "failed":
                msg = c.get("longrepr") or "collection failed"
                raise BuildError(node_id, f"test collection failed: {msg.splitlines()[0]}")

        results: list[TestResult] = []
        for t in report.get("tests", []):
            nodeid = t.get("nodeid", "")
            # nodeid format: "test_<node_id>.py::test_<name>"
            func = nodeid.rsplit("::", 1)[-1]
            if not func.startswith("test_"):
                continue
            test_name = func[len("test_") :]
            outcome = t.get("outcome")
            if outcome == "passed":
                results.append(TestResult(name=test_name, status=TestStatus.PASSING, detail=None))
            elif outcome in ("failed", "error"):
                longrepr = t.get("call", {}).get("longrepr") or t.get("longrepr") or ""
                first_line = longrepr.splitlines()[0] if longrepr else outcome
                results.append(TestResult(name=test_name, status=TestStatus.FAILING, detail=first_line))
            else:
                results.append(TestResult(name=test_name, status=TestStatus.UNRUN, detail=None))

        return results
