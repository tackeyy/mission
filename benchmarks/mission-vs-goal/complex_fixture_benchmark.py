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
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


MAX_EVALUATOR_OUTPUT_BYTES = 65536


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + __import__("hashlib").sha256(value).hexdigest()


def _load_generator(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("complex_fixture_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("complex fixture generator unavailable")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator


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
    shown = subprocess.run(
        ["git", "show", f"{source_commit}:{generator_path.relative_to(repo_root)}"],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if shown.returncode != 0 or shown.stdout != generator_path.read_bytes():
        raise ValueError("fixture generator does not match the declared commit")
    generator = _load_generator(generator_path)
    catalog_bytes = generator.render_catalog_bytes()
    catalog = generator.render_catalog()
    entries = {entry["id"]: entry for entry in catalog["tasks"]}
    entry = entries.get(task_id)
    if not isinstance(entry, dict):
        raise ValueError("unknown fixture task")
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
    generated_digest = _digest_tree(task_root)
    input_identity = _digest_bytes(json.dumps({
        "source_commit": source_commit,
        "generator_digest": _digest_bytes(shown.stdout),
        "catalog_digest": _digest_bytes(catalog_bytes),
        "task_id": task_id,
        "family": entry["family"],
        "version": entry["version"],
        "fixture_group": group,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "source_commit": source_commit,
        "generator_digest": _digest_bytes(shown.stdout),
        "catalog_digest": _digest_bytes(catalog_bytes),
        "input_digest": generator.template_digest(task_id, group),
        "input_manifest_identity": input_identity,
        "task_id": task_id,
        "family": entry["family"],
        "version": entry["version"],
        "fixture_group": group,
        "generated_commit": generated_commit,
        "task_root": task_id,
        "generated_digest": generated_digest,
        "worker_digest": generated_digest,
        "candidate_digest": generated_digest,
    }


def load_catalog(root: Path) -> list[dict[str, Any]]:
    generator = _load_generator(root.parent / "generate_complex_fixtures.py")
    raw = generator.render_catalog()
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
    """Bound the direct child and ordinary process-group descendants.

    A descendant can retain inherited pipe descriptors after its direct parent
    exits.  This terminates that original process group and closes this
    supervisor's descriptors without claiming to contain a descendant that
    deliberately creates a new session.
    """
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = False

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

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        selector.register(stream, selectors.EVENT_READ, name)
    while time.monotonic() < deadline:
        remaining = max(0, deadline - time.monotonic())
        for key, _ in selector.select(min(remaining, 0.02)):
            chunk = os.read(key.fileobj.fileno(), 8192)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            name = key.data
            if len(captured[name]) + len(chunk) > MAX_EVALUATOR_OUTPUT_BYTES:
                exceeded = True
                terminate_group()
                break
            captured[name].extend(chunk)
        if exceeded:
            break
        if process.poll() is not None and not selector.get_map():
            break
    incomplete = bool(selector.get_map())
    if process.poll() is None and not exceeded:
        timed_out = True

    if timed_out or exceeded or incomplete:
        terminate_group()

    shutdown_deadline = time.monotonic() + 0.1
    while process.poll() is None and time.monotonic() < shutdown_deadline:
        time.sleep(0.002)
    selector.close()
    for stream in (process.stdout, process.stderr):
        try:
            stream.detach().close()
        except (OSError, ValueError):
            pass
    return process.poll(), bytes(captured["stdout"]), timed_out, exceeded, incomplete


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
                if output_exceeded:
                    return {**base, "status": "failed", "reason": "evaluator_output_too_large", "case_count": len(checks), "cases": cases}
                if incomplete:
                    return {**base, "status": "blocked", "reason": "evaluator_reader_incomplete", "case_count": len(checks), "cases": cases}
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


def export_worker_fixtures(repo_root: Path, starting_commit: str, destination: Path, catalog_root: Path | None = None) -> Path:
    """Generate one-task repos, then use #882's unchanged positive export."""
    module_path = repo_root / "benchmarks" / "mission-vs-goal" / "native_goal_benchmark.py"
    spec = importlib.util.spec_from_file_location("native_goal_worker_export", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("worker export implementation unavailable")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    generated_catalog_root = repo_root / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    if catalog_root is not None and catalog_root.resolve() != generated_catalog_root.resolve():
        raise ValueError("caller-supplied catalog root is not the fixed fixture source")
    entries = load_catalog(generated_catalog_root)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for entry in entries:
        with tempfile.TemporaryDirectory(prefix="mission-complex-worker-") as temporary:
            generated = Path(temporary) / "repo"
            record = materialize_task(repo_root, starting_commit, entry["id"], "worker", generated)
            exported = module.create_worker_export(generated, record["generated_commit"], destination / entry["id"], [entry["id"]])
            record["export_digest"] = _digest_tree(exported / entry["id"])
            manifest.append(record)
    if not manifest:
        raise ValueError("complex fixture export requires assignments")
    (destination / "manifest.json").write_text(json.dumps({
        "schema": "mission-complex-fixture-export/1",
        "source_commit": starting_commit,
        "generator_digest": manifest[0]["generator_digest"],
        "catalog_digest": manifest[0]["catalog_digest"],
        "assignment_count": len(manifest),
        "assignments": manifest,
    }, indent=2) + "\n", encoding="utf-8")
    return destination
