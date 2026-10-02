#!/usr/bin/env python3
"""External, fail-closed evaluator for the #883 complex repair fixtures.

The evaluator is deliberately separate from worker exports.  It is visible to
repository readers, so it is called an external evaluator rather than hidden.
Each candidate is loaded in a fresh interpreter and its observed result is
compared with an evaluator-owned contract; a candidate's own success marker is
not trusted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return "sha256:" + digest.hexdigest()


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
print(json.dumps(module.scenario(), sort_keys=True))
'''


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
            observed = subprocess.run([sys.executable, "-I", str(runner), str(candidate)], text=True, capture_output=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            return {**base, "status": "blocked", "reason": "evaluator_timeout", "case_count": len(checks), "cases": []}
    if observed.returncode != 0:
        return {**base, "status": "failed", "reason": "evaluator_execution_failed", "case_count": len(checks), "cases": []}
    try:
        actual = json.loads(observed.stdout)
    except json.JSONDecodeError:
        return {**base, "status": "failed", "reason": "evaluator_output_invalid", "case_count": len(checks), "cases": []}
    cases = [{"name": check.get("name"), "passed": actual == check.get("expected")} for check in checks if isinstance(check, dict)]
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
