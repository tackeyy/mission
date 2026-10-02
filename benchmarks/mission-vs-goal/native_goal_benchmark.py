#!/usr/bin/env python3
"""Native Goal observation primitives for the additive #882 benchmark schema.

The historical benchmark runners deliberately retain their old result schema.
This module records only observed native-goal protocol facts: it never turns an
inline prompt into a native Goal result, and never promotes an event to success
without a terminal goal read-back.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable


BENCH_DIR = Path(__file__).resolve().parent
HISTORICAL_RESULT_SCHEMA = BENCH_DIR / "result.schema.json"
NATIVE_SCHEMA = "native-goal-benchmark-result/1"
MANIFEST_SCHEMA = "native-goal-benchmark-manifest/1"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_UNSAFE_TRACE = ("/Users/", "/.codex/memories/", "/.claude/projects/")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s\"']+")


def _goal(response: dict[str, Any] | None) -> dict[str, Any] | None:
    value = response.get("goal") if isinstance(response, dict) else None
    return value if isinstance(value, dict) else None


def observe_codex_goal(
    thread_id: str,
    objective: str,
    set_response: dict[str, Any] | None,
    get_response: dict[str, Any] | None,
    events: Iterable[dict[str, Any]],
    expected_turn_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Verify set/get identity and terminal state independently from events."""
    created, observed = _goal(set_response), _goal(get_response)
    target_events = [event for event in events if isinstance(event, dict) and isinstance(event.get("params"), dict) and event["params"].get("threadId") == thread_id]
    if expected_turn_ids is not None:
        target_events = [event for event in target_events if event["params"].get("turnId") in expected_turn_ids]
    target_methods = [event.get("method") for event in target_events]
    started_index = next((index for index, method in enumerate(target_methods) if method == "turn/started"), None)
    completed_index = next((index for index, method in enumerate(target_methods) if method == "turn/completed"), None)
    base = {
        "native_goal_observed": False,
        "goal_thread_id": observed.get("threadId") if observed else None,
        "turn_started": started_index is not None,
        "turn_completed": completed_index is not None,
        "goal_status": observed.get("status") if observed else None,
        "tokens_used": observed.get("tokensUsed") if observed else None,
        "token_budget": observed.get("tokenBudget") if observed else None,
    }
    if created is None or observed is None:
        return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "goal_not_observed"}
    created_at = created.get("createdAt") if created else None
    observed_created_at = observed.get("createdAt") if observed else None
    if (not isinstance(created_at, int) or isinstance(created_at, bool) or created_at < 0
            or not isinstance(observed_created_at, int) or isinstance(observed_created_at, bool) or observed_created_at < 0
            or any(goal.get("threadId") != thread_id or goal.get("objective") != objective for goal in (created, observed))
            or observed_created_at != created_at):
        return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "goal_identity_mismatch"}
    base["native_goal_observed"] = True
    if not expected_turn_ids or started_index is None or completed_index is None or completed_index < started_index:
        return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "turn_not_completed"}
    if observed.get("status") in {"budgetLimited", "usageLimited"}:
        reason = "goal_budget_limited" if observed["status"] == "budgetLimited" else "goal_usage_limited"
        return {**base, "fidelity": "verified", "outcome": "blocked", "reason": reason}
    if observed.get("status") != "complete":
        return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "goal_not_complete"}
    return {**base, "fidelity": "verified", "outcome": "completed", "reason": None}


def observe_claude_goal(invocation: str, returncode: int, result: dict[str, Any] | None) -> dict[str, Any]:
    """Require an official `/goal` invocation plus terminal structured output."""
    result = result if isinstance(result, dict) else {}
    invoked = invocation.lstrip().startswith("/goal ")
    terminal = isinstance(result.get("session_id"), str) and bool(result.get("result")) and result.get("is_error") is False
    # Claude print JSON has no native Goal id or lifecycle read-back.  It may
    # establish that `/goal` was sent, but must never establish completion.
    if invoked and returncode == 0 and terminal:
        return {"native_goal_observed": True, "fidelity": "unverified", "outcome": "failed", "reason": "goal_lifecycle_unobserved"}
    reason = "official_goal_not_invoked" if not invoked else "terminal_goal_observation_missing"
    return {"native_goal_observed": invoked, "fidelity": "unverified", "outcome": "failed", "reason": reason}


def preserve_assignment_outcomes(plan: Iterable[tuple[str, str, str]], records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialise every uniquely identified assignment without dropping attempts."""
    by_assignment: dict[str, dict[str, Any]] = {}
    for record in records:
        assignment_id = record.get("assignment_id")
        if not isinstance(assignment_id, str) or not assignment_id or assignment_id in by_assignment:
            raise ValueError("records require distinct assignment_id values")
        by_assignment[assignment_id] = dict(record)
    outcomes = []
    planned: set[str] = set()
    for assignment_id, task_id, arm in plan:
        if not assignment_id or assignment_id in planned:
            raise ValueError("plan requires distinct assignment_id values")
        planned.add(assignment_id)
        record = by_assignment.pop(assignment_id, None)
        if record is not None:
            if record.get("task_id") != task_id or record.get("arm") != arm:
                raise ValueError("assignment record does not match its planned cell")
            outcomes.append(record)
        else:
            outcomes.append({
            "assignment_id": assignment_id, "task_id": task_id, "arm": arm,
            "outcome": "not_started", "reason": "missing_assignment_record",
            })
    if by_assignment:
        raise ValueError("records include assignment_id outside the plan")
    return outcomes


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest_tree(root: Path, *, exclude_git: bool = False) -> str:
    """Hash a stable regular-file tree without following links."""
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError("candidate tree unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("candidate tree is not a regular directory")
    files: list[tuple[Path, os.stat_result]] = []
    directories: list[tuple[Path, os.stat_result]] = [(root, root_stat)]
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ValueError("candidate tree unreadable") from exc
        for entry in entries:
            relative = Path(entry.path).relative_to(root)
            if exclude_git and ".git" in relative.parts:
                continue
            try:
                item = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError("candidate tree changed during snapshot") from exc
            path = Path(entry.path)
            if stat.S_ISDIR(item.st_mode):
                directories.append((path, item)); pending.append(path)
            elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
                files.append((path, item))
            else:
                raise ValueError("candidate tree contains link or special entry")
    digest = hashlib.sha256()
    for path, before in sorted(files):
        relative = path.relative_to(root)
        rel = relative.as_posix().encode("utf-8")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read()
            after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError("candidate tree changed during snapshot") from exc
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError("candidate tree changed during snapshot")
        digest.update(len(rel).to_bytes(8, "big")); digest.update(rel)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    for path, before in directories:
        try:
            after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError("candidate tree changed during snapshot") from exc
        if (after.st_dev, after.st_ino, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_mtime_ns):
            raise ValueError("candidate tree changed during snapshot")
    return "sha256:" + digest.hexdigest()


def immutable_manifest(starting_commit: str, source_dir: Path, package_path: Path, conditions: dict[str, Any]) -> dict[str, Any]:
    """Bind one full commit, source tree, package bytes, and declared conditions."""
    if not _COMMIT_RE.fullmatch(starting_commit):
        raise ValueError("starting_commit must be a full immutable commit SHA")
    if not source_dir.is_dir() or not package_path.is_file():
        raise ValueError("immutable source directory and package file are required")
    return {
        "schema": MANIFEST_SCHEMA,
        "starting_commit": starting_commit,
        "source": {"path": source_dir.name, "sha256": _digest_tree(source_dir)},
        "package": {"path": package_path.name, "sha256": _digest_file(package_path)},
        "conditions": dict(conditions),
    }


def create_immutable_package(repo_root: Path, starting_commit: str, output_path: Path) -> Path:
    """Archive the shipped Mission sources from one commit without changing sync guards.

    `git archive` reads the immutable object directly.  It neither checks out a
    branch nor writes user configuration; an atomic replace prevents a failed
    archive from being confused with a usable package.
    """
    if not _COMMIT_RE.fullmatch(starting_commit):
        raise ValueError("starting_commit must be a full immutable commit SHA")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".mission-package-", suffix=".tmp", dir=output_path.parent, delete=False,
    ) as temporary:
        tmp_path = Path(temporary.name)
        result = subprocess.run(
            ["git", "archive", "--format=tar", starting_commit, "plugins/mission", "skills/mission"],
            cwd=repo_root, stdout=temporary, stderr=subprocess.PIPE, check=False,
        )
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git archive failed: {detail}")
    tmp_path.replace(output_path)
    return output_path


def create_worker_export(repo_root: Path, starting_commit: str, output_path: Path, allowed_paths: Iterable[str]) -> Path:
    """Create the only task tree handed to a worker from an explicit allowlist.

    Every retained source file must be beneath a task-contract allowlist path.
    This is a positive, mechanically checked export boundary rather than a
    filename blocklist; its tree digest is recorded by the caller.
    """
    paths = list(allowed_paths)
    if not _COMMIT_RE.fullmatch(starting_commit) or not paths:
        raise ValueError("immutable commit and worker allowlist are required")
    output_path.mkdir(parents=True, exist_ok=False)
    completed = False
    archive: Path | None = None
    with tempfile.NamedTemporaryFile(prefix=".worker-export-", suffix=".tar", dir=output_path.parent, delete=False) as temporary:
        archive = Path(temporary.name)
        result = subprocess.run(["git", "archive", "--format=tar", starting_commit], cwd=repo_root, stdout=temporary, stderr=subprocess.PIPE, check=False)
    try:
        if result.returncode != 0:
            raise RuntimeError("task export archive failed")
        allowed: list[Path] = []
        for raw_path in paths:
            relative = Path(raw_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("worker allowlist path escapes export")
            allowed.append(relative)
        with tarfile.open(archive, "r") as source:
            members = source.getmembers()
            names: set[Path] = set()
            for member in members:
                relative = Path(member.name)
                if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                        or member.issym() or member.islnk() or not (member.isfile() or member.isdir())
                        or relative in names):
                    raise ValueError("unsafe archive member in worker export")
                names.add(relative)
            for prefix in allowed:
                if not any(name == prefix or prefix in name.parents for name in names):
                    raise ValueError("worker allowlist path absent from export")
            retained = [member for member in members if any(Path(member.name) == prefix or prefix in Path(member.name).parents for prefix in allowed)]
            source.extractall(output_path, members=retained, filter="data")
        if any(path.is_symlink() for path in output_path.rglob("*")):
            raise RuntimeError("worker export retained a link")
        completed = True
        return output_path
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
        if not completed:
            shutil.rmtree(output_path, ignore_errors=True)


def worker_export_manifest(worker_root: Path) -> dict[str, Any]:
    return {"schema": "mission-worker-export/1", "sha256": _digest_tree(worker_root, exclude_git=True)}


def initialize_worker_export_repository(worker_root: Path) -> str:
    """Give the filtered export its own history-free revision scope."""
    commands = (
        ["git", "init", "--quiet"],
        ["git", "add", "--all"],
        ["git", "-c", "user.name=benchmark", "-c", "user.email=benchmark@invalid", "commit", "--quiet", "-m", "benchmark export"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=worker_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            raise RuntimeError("worker export repository initialization failed")
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worker_root, text=True, capture_output=True, check=False)
    commit = result.stdout.strip()
    if result.returncode != 0 or not _COMMIT_RE.fullmatch(commit):
        raise RuntimeError("worker export commit unavailable")
    return commit


def write_record(path: Path, record: dict[str, Any]) -> None:
    """Persist one sanitised assignment record without replacing earlier evidence."""
    def sanitise(value: Any, key: str | None = None) -> Any:
        if key in {"objective", "acceptance_criterion"} and isinstance(value, str):
            return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
        if isinstance(value, dict):
            return {name: sanitise(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [sanitise(item) for item in value]
        if isinstance(value, str):
            return _EMAIL_RE.sub("[redacted-email]", _BEARER_RE.sub("[redacted-bearer]", value))
        return value
    serialised = json.dumps(sanitise(record), ensure_ascii=False, sort_keys=True)
    if any(marker in serialised for marker in _UNSAFE_TRACE):
        raise ValueError("unsafe trace data in benchmark record")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(serialised + "\n")
