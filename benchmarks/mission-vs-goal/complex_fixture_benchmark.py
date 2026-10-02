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
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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


def materialize_task(repo_root: Path, source_commit: str, task_id: str, group: str, destination: Path) -> dict[str, Any]:
    """Bind fixed H inputs, then create one generated task repository."""
    generator_path = repo_root / "benchmarks" / "mission-vs-goal" / "generate_complex_fixtures.py"
    catalog_path = repo_root / "benchmarks" / "mission-vs-goal" / "complex-fixtures" / "catalog.json"
    for path in (generator_path, catalog_path):
        shown = subprocess.run(["git", "show", f"{source_commit}:{path.relative_to(repo_root)}"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if shown.returncode != 0 or shown.stdout != path.read_bytes():
            raise ValueError("fixture source does not match the declared commit")
    spec = importlib.util.spec_from_file_location("complex_fixture_generator", generator_path)
    generator = importlib.util.module_from_spec(spec); spec.loader.exec_module(generator)
    files = generator.task_template(task_id, group)
    task_root = destination / task_id; task_root.mkdir(parents=True)
    for name, content in files.items():
        (task_root / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=destination, check=True)
    subprocess.run(["git", "config", "user.name", "fixture"], cwd=destination, check=True)
    subprocess.run(["git", "add", task_id], cwd=destination, check=True)
    subprocess.run(["git", "commit", "-qm", "generated fixture"], cwd=destination, check=True)
    generated_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=destination, text=True, capture_output=True, check=True).stdout.strip()
    return {"source_commit": source_commit, "input_digest": generator.template_digest(task_id, group), "task_id": task_id,
            "generated_commit": generated_commit, "task_root": task_id, "worker_digest": _digest_tree(task_root)}


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


def _materialize_candidate(source: Path, destination: Path) -> tuple[Path, str]:
    """Copy only a stable regular-file candidate tree for one evaluator run."""
    source_digest = _digest_tree(source)
    destination.mkdir()
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    destination_digest = _digest_tree(destination)
    if _digest_tree(source) != source_digest or destination_digest != source_digest:
        raise ValueError("candidate snapshot changed during materialization")
    return destination, source_digest


def _run_bounded(command: list[str], *, timeout_seconds: float) -> tuple[int | None, bytes, bool, bool, bool]:
    """Run one evaluator child with bounded in-memory stdout/stderr collection."""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def terminate_group() -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except (PermissionError, ProcessLookupError):
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

    def consume(name: str, stream: Any) -> None:
        while chunk := stream.read(8192):
            if len(captured[name]) + len(chunk) > MAX_EVALUATOR_OUTPUT_BYTES:
                exceeded.set()
                terminate_group()
                return
            captured[name].extend(chunk)

    readers = [threading.Thread(target=consume, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None and time.monotonic() < deadline:
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        timed_out = True
        terminate_group()
        process.wait()
    for reader in readers:
        reader.join(timeout=max(0, deadline - time.monotonic()))
    incomplete = any(reader.is_alive() for reader in readers)
    if incomplete:
        terminate_group()
        for stream in (process.stdout, process.stderr):
            stream.close()
        for reader in readers:
            reader.join(timeout=0.1)
    return process.returncode, bytes(captured["stdout"]), timed_out, exceeded.is_set(), incomplete


def evaluate_candidate(root: Path, entry: dict[str, Any], candidate: Path, timeout_seconds: float = 3.0) -> dict[str, Any]:
    """Observe one candidate in a fresh process and preserve non-pass states."""
    base = {"task_id": entry.get("id"), "family": entry.get("family"), "version": entry.get("version"), "candidate_digest": None}
    checks = entry.get("checks")
    if not isinstance(checks, list) or not checks:
        return {**base, "status": "failed", "reason": "no_evaluation_cases", "case_count": 0, "cases": []}
    if not candidate.is_dir():
        return {**base, "status": "failed", "reason": "candidate_missing", "case_count": len(checks), "cases": []}
    try:
        initial_digest = _digest_tree(candidate)
    except ValueError:
        return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": []}
    base["candidate_digest"] = initial_digest
    with tempfile.TemporaryDirectory(prefix="mission-complex-evaluator-") as temporary:
        runner = Path(temporary) / "runner.py"; runner.write_text(_runner_source(), encoding="utf-8")
        try:
            cases = []
            for check in checks:
                scenario = check.get("scenario", check.get("operations"))
                materialized, snapshot_digest = _materialize_candidate(candidate, Path(temporary) / f"candidate-{len(cases)}")
                if snapshot_digest != initial_digest:
                    return {**base, "status": "failed", "reason": "candidate_changed", "case_count": len(checks), "cases": cases}
                returncode, stdout, timed_out, output_exceeded, incomplete = _run_bounded(
                    [sys.executable, "-I", str(runner), str(materialized), json.dumps(scenario)],
                    timeout_seconds=timeout_seconds,
                )
                if timed_out:
                    return {**base, "status": "blocked", "reason": "evaluator_timeout", "case_count": len(checks), "cases": cases}
                if incomplete:
                    return {**base, "status": "blocked", "reason": "evaluator_reader_incomplete", "case_count": len(checks), "cases": cases}
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
    try:
        final_digest = _digest_tree(candidate)
    except ValueError:
        return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": cases}
    if final_digest != initial_digest:
        return {**base, "status": "failed", "reason": "candidate_changed", "case_count": len(checks), "cases": cases}
    if len(cases) != len(checks) or not cases:
        return {**base, "status": "failed", "reason": "evaluator_cases_invalid", "case_count": len(cases), "cases": cases}
    return {**base, "status": "passed" if all(case["passed"] for case in cases) else "failed",
            "reason": None if all(case["passed"] for case in cases) else "contract_mismatch",
            "case_count": len(cases), "cases": cases}


def evaluate_assignment(catalog_root: Path, entry: dict[str, Any], task_root: Path, exported_task_root: Path, timeout_seconds: float = 3.0) -> dict[str, Any]:
    """Evaluate one explicit task root against its separately exported task root."""
    task_id = entry.get("id")
    if not isinstance(task_id, str) or task_root.name != task_id or exported_task_root.name != task_id:
        return {"task_id": task_id, "family": entry.get("family"), "version": entry.get("version"), "candidate_digest": None,
                "status": "failed", "reason": "assignment_task_root_mismatch", "case_count": 0, "cases": []}
    return evaluate_candidate(catalog_root, entry, exported_task_root, timeout_seconds)


def run_public_smoke(candidate: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", "import runpy, sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[1] + '/public_smoke.py')", str(candidate)],
        cwd=candidate, text=True, capture_output=True, timeout=3, check=False,
    )
    return {"status": "passed" if completed.returncode == 0 else "failed"}


def export_worker_fixtures(repo_root: Path, starting_commit: str, destination: Path, catalog_root: Path) -> Path:
    """Generate one-task repos, then use #882's unchanged positive export."""
    module_path = repo_root / "benchmarks" / "mission-vs-goal" / "native_goal_benchmark.py"
    spec = importlib.util.spec_from_file_location("native_goal_worker_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("worker export implementation unavailable")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    entries = load_catalog(catalog_root)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for entry in entries:
        with tempfile.TemporaryDirectory(prefix="mission-complex-worker-") as temporary:
            generated = Path(temporary) / "repo"
            record = materialize_task(repo_root, starting_commit, entry["id"], "worker", generated)
            exported = module.create_worker_export(generated, record["generated_commit"], destination / entry["id"], [entry["id"]])
            record["export_digest"] = _digest_tree(exported / entry["id"])
            manifest.append(record)
    (destination / "manifest.json").write_text(json.dumps({"source_commit": starting_commit, "assignments": manifest}, indent=2) + "\n", encoding="utf-8")
    return destination
