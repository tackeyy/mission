#!/usr/bin/env python3
"""External, fail-closed evaluator for the #883 complex repair fixtures.

The evaluator is deliberately separate from worker exports.  It is visible to
repository readers, so it is called an external evaluator rather than hidden.
Each candidate is loaded in a fresh interpreter and its observed result is
compared with an evaluator-owned contract; a candidate's own success marker is
not trusted.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import shutil
from pathlib import Path
from typing import Any


MAX_EVALUATOR_OUTPUT_BYTES = 65536


def _digest_tree(root: Path) -> str:
    """Use #882's regular-file, no-link snapshot boundary verbatim."""
    source = Path(__file__).with_name("native_goal_benchmark.py")
    spec = importlib.util.spec_from_file_location("native_goal_snapshot", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("worker snapshot implementation unavailable")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module._digest_tree(root, exclude_git=True)


def load_catalog(root: Path) -> list[dict[str, Any]]:
    raw = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks, list) or not all(isinstance(task, dict) for task in tasks):
        raise ValueError("complex fixture catalog is malformed")
    ids = [task.get("id") for task in tasks]
    if len(tasks) != 12 or len(set(ids)) != len(ids):
        raise ValueError("complex fixture catalog requires twelve unique tasks")
    for task in tasks:
        if not all(isinstance(task.get(key), str) and task[key] for key in ("id", "family", "version", "requirement", "realistic_failure")):
            raise ValueError("complex fixture task is incomplete")
        if not isinstance(task.get("checks"), list):
            raise ValueError("complex fixture checks are malformed")
    return tasks


def _runner_source() -> str:
    return '''import importlib.util, json, sys
candidate = sys.argv[1]
spec = importlib.util.spec_from_file_location("candidate_service", candidate + "/service.py")
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, candidate)
spec.loader.exec_module(module)
print(json.dumps(module.execute(json.loads(sys.argv[2])), sort_keys=True))
'''


def _materialize_candidate(source: Path, destination: Path) -> Path:
    """Copy only a stable regular-file candidate tree for one evaluator run."""
    _digest_tree(source)
    destination.mkdir()
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    _digest_tree(destination)
    return destination


def _run_bounded(command: list[str], *, timeout_seconds: float) -> tuple[int | None, bytes, bool, bool]:
    """Run one evaluator child with bounded in-memory stdout/stderr collection."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def consume(name: str, stream: Any) -> None:
        while chunk := stream.read(8192):
            if len(captured[name]) + len(chunk) > MAX_EVALUATOR_OUTPUT_BYTES:
                exceeded.set()
                process.kill()
                return
            captured[name].extend(chunk)

    readers = [threading.Thread(target=consume, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for reader in readers:
        reader.join()
    return process.returncode, bytes(captured["stdout"]), timed_out, exceeded.is_set()


def evaluate_candidate(root: Path, entry: dict[str, Any], candidate: Path, timeout_seconds: float = 3.0) -> dict[str, Any]:
    """Observe one candidate in a fresh process and preserve non-pass states."""
    base = {"task_id": entry.get("id"), "family": entry.get("family"), "version": entry.get("version"),
            "candidate_digest": _digest_tree(candidate) if candidate.is_dir() else None}
    checks = entry.get("checks")
    if not isinstance(checks, list) or not checks:
        return {**base, "status": "failed", "reason": "no_evaluation_cases", "case_count": 0, "cases": []}
    if not candidate.is_dir():
        return {**base, "status": "failed", "reason": "candidate_missing", "case_count": len(checks), "cases": []}
    with tempfile.TemporaryDirectory(prefix="mission-complex-evaluator-") as temporary:
        runner = Path(temporary) / "runner.py"; runner.write_text(_runner_source(), encoding="utf-8")
        try:
            cases = []
            for check in checks:
                scenario = check.get("scenario", check.get("operations"))
                materialized = _materialize_candidate(candidate, Path(temporary) / f"candidate-{len(cases)}")
                returncode, stdout, timed_out, output_exceeded = _run_bounded(
                    [sys.executable, "-I", str(runner), str(materialized), json.dumps(scenario)],
                    timeout_seconds=timeout_seconds,
                )
                if timed_out:
                    return {**base, "status": "blocked", "reason": "evaluator_timeout", "case_count": len(checks), "cases": cases}
                if output_exceeded:
                    return {**base, "status": "failed", "reason": "evaluator_output_too_large", "case_count": len(checks), "cases": cases}
                if returncode != 0:
                    return {**base, "status": "failed", "reason": "evaluator_execution_failed", "case_count": len(checks), "cases": cases}
                try:
                    actual = json.loads(stdout)
                except json.JSONDecodeError:
                    return {**base, "status": "failed", "reason": "evaluator_output_invalid", "case_count": len(checks), "cases": cases}
                cases.append({"name": check.get("name"), "passed": actual == check.get("expected")})
        except ValueError:
            return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": cases}
    if len(cases) != len(checks) or not cases:
        return {**base, "status": "failed", "reason": "evaluator_cases_invalid", "case_count": len(cases), "cases": cases}
    return {**base, "status": "passed" if all(case["passed"] for case in cases) else "failed",
            "reason": None if all(case["passed"] for case in cases) else "contract_mismatch",
            "case_count": len(cases), "cases": cases}


def run_public_smoke(candidate: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", "import runpy, sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[1] + '/public_smoke.py')", str(candidate)],
        cwd=candidate, text=True, capture_output=True, timeout=3, check=False,
    )
    return {"status": "passed" if completed.returncode == 0 else "failed"}


def export_worker_fixtures(repo_root: Path, starting_commit: str, destination: Path, catalog_root: Path) -> Path:
    """Use #882's positive allowlist export for the exact worker material."""
    module_path = repo_root / "benchmarks" / "mission-vs-goal" / "native_goal_benchmark.py"
    spec = importlib.util.spec_from_file_location("native_goal_worker_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("worker export implementation unavailable")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    allowed = [f"benchmarks/mission-vs-goal/complex-fixtures/worker/{entry['id']}" for entry in load_catalog(catalog_root)]
    return module.create_worker_export(repo_root, starting_commit, destination, allowed)
