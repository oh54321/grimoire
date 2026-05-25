"""Runs pytest against materialized build files and parses the JSON report.

Two execution paths, selected by `use_worker`:
- a long-lived warm worker process (default), reused across runs; and
- a one-shot subprocess per run (fallback for debugging).
Both preserve subprocess isolation and produce identical results.
"""

import json
import os
import select
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import library
from library.errors import BuildError
from library.ids import NodeId
from library.nodes import TestStatus


@dataclass
class TestResult:
    __test__ = False  # tell pytest not to collect this dataclass as a test class

    name: str
    status: TestStatus
    detail: str | None


def _worker_env(store_root: Path) -> dict:
    """Environment with `library` (src dir) and the store root on PYTHONPATH so
    both `import library._test_worker` and `import build.X` resolve."""
    src_dir = str(Path(library.__file__).resolve().parent.parent)
    env = dict(os.environ)
    parts = [src_dir, str(store_root)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


class _TestWorker:
    """Long-lived warm pytest process. One per Runner. Respawned on crash."""

    MAX_RUNS = 100

    def __init__(self, store_root: Path, python: str, timeout: float) -> None:
        self._store_root = store_root
        self._python = python
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._runs = 0

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            [self._python, "-m", "library._test_worker", str(self._store_root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            cwd=str(self._store_root), env=_worker_env(self._store_root), bufsize=1,
        )
        self._runs = 0

    def run(self, target: Path, report: Path, node_id: NodeId) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        req = json.dumps({"target": str(target), "report": str(report)}) + "\n"
        try:
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            self._spawn()
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
        ready, _, _ = select.select([self._proc.stdout], [], [], self._timeout)
        if not ready:
            self.kill()
            raise BuildError(node_id, f"test run timed out after {self._timeout}s")
        reply = self._proc.stdout.readline()
        if not reply:
            self.kill()
            raise BuildError(node_id, "test worker died during run")
        self._runs += 1
        if self._runs >= self.MAX_RUNS:
            self.kill()  # bound pytest global-state drift

    def kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None


class Runner:
    def __init__(self, build_root: Path, python: str | None = None,
                 use_worker: bool = True, timeout: float = 60.0) -> None:
        self.build_root = build_root
        self.python = python or sys.executable
        self.use_worker = use_worker
        self.timeout = timeout
        self._worker_obj: _TestWorker | None = None

    def _worker(self) -> _TestWorker:
        if self._worker_obj is None:
            self._worker_obj = _TestWorker(self.build_root.parent, self.python, self.timeout)
        return self._worker_obj

    def run_tests(self, node_id: NodeId) -> list[TestResult]:
        target = self.build_root / f"test_{node_id}.py"
        if not target.exists():
            return []
        report_path = self.build_root / f".last_report_{node_id}.json"
        if report_path.exists():
            report_path.unlink()

        if self.use_worker:
            self._worker().run(target, report_path, node_id)
        else:
            self._run_oneshot(target, report_path, node_id)

        if not report_path.exists():
            raise BuildError(node_id, "pytest produced no JSON report")
        try:
            report = json.loads(report_path.read_text())
        finally:
            report_path.unlink(missing_ok=True)
        return self._parse_report(report, node_id)

    def _run_oneshot(self, target: Path, report_path: Path, node_id: NodeId) -> None:
        argv = [
            self.python, "-m", "pytest", str(target), "-q", "--no-header",
            "--json-report", f"--json-report-file={report_path}",
            "--json-report-omit=streams,warnings,keywords",
        ]
        cwd = self.build_root.parent
        env = _worker_env(cwd)
        subprocess.run(argv, capture_output=True, text=True, cwd=str(cwd), env=env)

    def _parse_report(self, report: dict, node_id: NodeId) -> list[TestResult]:
        for c in report.get("collectors", []):
            if c.get("outcome") == "failed":
                msg = c.get("longrepr") or "collection failed"
                raise BuildError(node_id, f"test collection failed: {msg.splitlines()[0]}")
        results: list[TestResult] = []
        for t in report.get("tests", []):
            func = t.get("nodeid", "").rsplit("::", 1)[-1]
            if not func.startswith("test_"):
                continue
            name = func[len("test_"):]
            outcome = t.get("outcome")
            if outcome == "passed":
                results.append(TestResult(name=name, status=TestStatus.PASSING, detail=None))
            elif outcome in ("failed", "error"):
                longrepr = t.get("call", {}).get("longrepr") or t.get("longrepr") or ""
                first = longrepr.splitlines()[0] if longrepr else outcome
                results.append(TestResult(name=name, status=TestStatus.FAILING, detail=first))
            else:
                results.append(TestResult(name=name, status=TestStatus.UNRUN, detail=None))
        return results
