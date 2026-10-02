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
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any


MAX_EVALUATOR_OUTPUT_BYTES = 65536
MAX_JSON_DEPTH = 64
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + __import__("hashlib").sha256(value).hexdigest()


def _load_generator(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("complex_fixture_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("complex fixture generator unavailable")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    return generator


def _load_verified_generator(path: Path, source: bytes) -> Any:
    """Execute only the exact generator bytes already bound to a commit."""
    module = types.ModuleType("complex_fixture_generator_verified")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def _fixed_generator(repo_root: Path, source_commit: str) -> tuple[Any, bytes]:
    if not _COMMIT_RE.fullmatch(source_commit):
        raise ValueError("fixture source commit must be a full immutable SHA")
    generator_path = repo_root / "benchmarks" / "mission-vs-goal" / "generate_complex_fixtures.py"
    shown = subprocess.run(
        ["git", "show", f"{source_commit}:{generator_path.relative_to(repo_root)}"],
        cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if shown.returncode != 0 or shown.stdout != generator_path.read_bytes():
        raise ValueError("fixture generator does not match the declared commit")
    return _load_verified_generator(generator_path, shown.stdout), shown.stdout


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
    generator, generator_bytes = _fixed_generator(repo_root, source_commit)
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
        "generator_digest": _digest_bytes(generator_bytes),
        "catalog_digest": _digest_bytes(catalog_bytes),
        "task_id": task_id,
        "family": entry["family"],
        "version": entry["version"],
        "fixture_group": group,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {
        "source_commit": source_commit,
        "generator_digest": _digest_bytes(generator_bytes),
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


def _json_value(value: Any) -> bool:
    """Accept only bounded, serializable JSON values without recursion leaks."""
    seen: set[int] = set()

    def valid(item: Any, depth: int) -> bool:
        if depth > MAX_JSON_DEPTH:
            return False
        if item is None or type(item) in (str, bool, int):
            return True
        if type(item) is float:
            return item == item and item not in (float("inf"), float("-inf"))
        if type(item) not in (list, dict):
            return False
        identity = id(item)
        if identity in seen:
            return False
        seen.add(identity)
        try:
            if type(item) is list:
                return all(valid(child, depth + 1) for child in item)
            return all(type(key) is str and valid(child, depth + 1) for key, child in item.items())
        finally:
            seen.remove(identity)

    try:
        if not valid(value, 0):
            return False
        json.dumps(value, allow_nan=False)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return False
    return True


def _json_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_json_equal(left, right) for left, right in zip(actual, expected))
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[key], expected[key]) for key in actual)
    return actual == expected


def _entry_error(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return "evaluation_entry_invalid"
    checks = entry.get("checks")
    if not isinstance(checks, list) or not checks:
        return "no_evaluation_cases"
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str) or "scenario" not in check or "expected" not in check:
            return "evaluation_case_invalid"
        if not _json_value(check["scenario"]) or not _json_value(check["expected"]):
            return "evaluation_case_non_json"
    return None


def _entry_metadata(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {"task_id": None, "family": None, "version": None}
    return {"task_id": entry.get("id"), "family": entry.get("family"), "version": entry.get("version")}


def _template_digest(generator: Any, task_id: str, group: str) -> str:
    """Digest the fixed template bytes without trusting an assignment's tree."""
    with tempfile.TemporaryDirectory(prefix="mission-complex-template-") as temporary:
        root = Path(temporary) / task_id
        root.mkdir()
        for name, content in generator.task_template(task_id, group).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return _digest_tree(root)


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

    def group_exists() -> bool:
        if os.name != "posix":
            return process.poll() is None
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

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
    while time.monotonic() < shutdown_deadline:
        if process.poll() is not None and not group_exists():
            break
        time.sleep(0.002)
    incomplete = incomplete or group_exists()
    selector.close()
    for stream in (process.stdout, process.stderr):
        try:
            stream.detach().close()
        except (OSError, ValueError):
            pass
    return process.poll(), bytes(captured["stdout"]), timed_out, exceeded, incomplete


def evaluate_candidate(root: Path, entry: Any, candidate: Path, timeout_seconds: float = 3.0, expected_candidate_digest: str | None = None) -> dict[str, Any]:
    """Observe one candidate in a fresh process and preserve non-pass states."""
    base = {**_entry_metadata(entry), "candidate_digest": None}
    error = _entry_error(entry)
    checks = entry.get("checks") if isinstance(entry, dict) else []
    if error is not None:
        return {**base, "status": "failed", "reason": error, "case_count": 0, "cases": []}
    if not candidate.is_dir():
        return {**base, "status": "failed", "reason": "candidate_missing", "case_count": len(checks), "cases": []}
    try:
        initial_digest = _digest_tree(candidate)
    except ValueError:
        return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": []}
    base["candidate_digest"] = initial_digest
    if expected_candidate_digest is not None and initial_digest != expected_candidate_digest:
        return {**base, "status": "failed", "reason": "candidate_digest_mismatch", "case_count": len(checks), "cases": []}
    with tempfile.TemporaryDirectory(prefix="mission-complex-evaluator-") as temporary:
        runner = Path(temporary) / "runner.py"; runner.write_text(_runner_source(), encoding="utf-8")
        cases = []
        try:
            for check in checks:
                scenario = check["scenario"]
                materialized, snapshot_digest = _materialize_candidate(candidate, Path(temporary) / f"candidate-{len(cases)}")
                if snapshot_digest != initial_digest or (expected_candidate_digest is not None and snapshot_digest != expected_candidate_digest):
                    return {**base, "status": "failed", "reason": "candidate_changed", "case_count": len(checks), "cases": cases}
                try:
                    returncode, stdout, timed_out, output_exceeded, incomplete = _run_bounded(
                        [sys.executable, "-I", str(runner), str(materialized), json.dumps(scenario)],
                        timeout_seconds=timeout_seconds,
                    )
                except OSError:
                    return {**base, "status": "failed", "reason": "evaluator_process_unavailable", "case_count": len(checks), "cases": cases}
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
                except (json.JSONDecodeError, TypeError, ValueError):
                    return {**base, "status": "failed", "reason": "evaluator_output_invalid", "case_count": len(checks), "cases": cases}
                if not _json_value(actual):
                    return {**base, "status": "failed", "reason": "evaluator_output_invalid", "case_count": len(checks), "cases": cases}
                cases.append({"name": check["name"], "passed": _json_equal(actual, check["expected"])})
        except (TypeError, ValueError):
            return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": cases}
    try:
        final_digest = _digest_tree(candidate)
    except ValueError:
        return {**base, "status": "failed", "reason": "candidate_invalid", "case_count": len(checks), "cases": cases}
    if final_digest != initial_digest or (expected_candidate_digest is not None and final_digest != expected_candidate_digest):
        return {**base, "status": "failed", "reason": "candidate_changed", "case_count": len(checks), "cases": cases}
    if len(cases) != len(checks) or not cases:
        return {**base, "status": "failed", "reason": "evaluator_cases_invalid", "case_count": len(cases), "cases": cases}
    return {**base, "status": "passed" if all(case["passed"] for case in cases) else "failed",
            "reason": None if all(case["passed"] for case in cases) else "contract_mismatch",
            "case_count": len(cases), "cases": cases}


def freeze_candidate(assignment: dict[str, Any], candidate: Path) -> dict[str, Any]:
    """Create the externally-owned candidate envelope after worker completion."""
    digest = _digest_tree(candidate)
    return {key: assignment[key] for key in ("task_id", "family", "version", "source_commit", "generator_digest", "catalog_digest", "input_manifest_identity")} | {"candidate_digest": digest}


def evaluate_assignment(repo_root: Path, source_commit: str, assignment: dict[str, Any], worker_root: Path, candidate_root: Path, candidate_envelope: dict[str, Any], timeout_seconds: float = 3.0) -> dict[str, Any]:
    """Evaluate a frozen candidate only when worker provenance matches fixed inputs."""
    base = {"task_id": assignment.get("task_id") if isinstance(assignment, dict) else None, "family": assignment.get("family") if isinstance(assignment, dict) else None, "version": assignment.get("version") if isinstance(assignment, dict) else None, "candidate_digest": None, "status": "failed", "reason": "assignment_invalid", "case_count": 0, "cases": []}
    try:
        generator, generator_bytes = _fixed_generator(repo_root, source_commit)
        catalog_bytes = generator.render_catalog_bytes()
        entries = {entry["id"]: entry for entry in generator.render_catalog()["tasks"]}
        task_id = assignment["task_id"]
        entry = entries[task_id]
        template_digest = _template_digest(generator, task_id, "worker")
        expected = {"source_commit": source_commit, "generator_digest": _digest_bytes(generator_bytes), "catalog_digest": _digest_bytes(catalog_bytes), "task_id": task_id, "family": entry["family"], "version": entry["version"], "fixture_group": "worker", "task_root": task_id, "input_digest": generator.template_digest(task_id, "worker"), "generated_digest": template_digest, "worker_digest": template_digest, "candidate_digest": template_digest}
        expected["input_manifest_identity"] = _digest_bytes(json.dumps({
            "source_commit": source_commit, "generator_digest": expected["generator_digest"],
            "catalog_digest": expected["catalog_digest"], "task_id": task_id,
            "family": entry["family"], "version": entry["version"], "fixture_group": "worker",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if any(assignment.get(key) != value for key, value in expected.items()):
            return {**base, "reason": "assignment_manifest_mismatch"}
        if not worker_root.is_dir() or worker_root.name != task_id or _digest_tree(worker_root) != template_digest:
            return {**base, "reason": "assignment_worker_mismatch"}
        envelope_keys = ("task_id", "family", "version", "source_commit", "generator_digest", "catalog_digest", "input_manifest_identity")
        if not isinstance(candidate_envelope, dict) or any(candidate_envelope.get(key) != assignment.get(key) for key in envelope_keys):
            return {**base, "reason": "candidate_envelope_mismatch"}
        if not candidate_root.is_dir() or _digest_tree(candidate_root) != candidate_envelope.get("candidate_digest"):
            return {**base, "reason": "candidate_digest_mismatch"}
    except (KeyError, TypeError, ValueError):
        return base
    return evaluate_candidate(
        repo_root / "benchmarks" / "mission-vs-goal" / "complex-fixtures",
        entry,
        candidate_root,
        timeout_seconds,
        expected_candidate_digest=candidate_envelope["candidate_digest"],
    )


def run_public_smoke(candidate: Path) -> dict[str, Any]:
    try:
        returncode, _output, timed_out, exceeded, incomplete = _run_bounded(
            [sys.executable, "-I", "-c", "import runpy, sys; sys.path.insert(0, sys.argv[1]); runpy.run_path(sys.argv[1] + '/public_smoke.py')", str(candidate)], timeout_seconds=3,
        )
    except OSError:
        return {"status": "failed", "reason": "smoke_process_unavailable"}
    if timed_out:
        return {"status": "failed", "reason": "smoke_timeout"}
    if exceeded:
        return {"status": "failed", "reason": "smoke_output_too_large"}
    if incomplete:
        return {"status": "failed", "reason": "smoke_reader_incomplete"}
    return {"status": "passed" if returncode == 0 else "failed", "reason": None if returncode == 0 else "smoke_execution_failed"}


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
