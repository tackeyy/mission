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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable


BENCH_DIR = Path(__file__).resolve().parent
HISTORICAL_RESULT_SCHEMA = BENCH_DIR / "result.schema.json"
NATIVE_SCHEMA = "native-goal-benchmark-result/1"
MANIFEST_SCHEMA = "native-goal-benchmark-manifest/1"
CODEX_REQUIRED_OPERATIONS = frozenset({"thread/goal/set", "thread/goal/get", "thread/goal/clear", "turn/start"})
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_UNSAFE_TRACE = ("/Users/", "/.codex/memories/", "/.claude/projects/")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s\"']+")


def _methods(value: Any) -> set[str]:
    """Collect JSON-RPC method strings from a schema-shaped value."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"method", "methods", "enum", "const"}:
                values = item if isinstance(item, list) else [item]
                found.update(v for v in values if isinstance(v, str) and "/" in v)
            found.update(_methods(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_methods(item))
    return found


def detect_codex_capability(schema: dict[str, Any], provider_version: str | None) -> dict[str, Any]:
    """Return native support only when every required app-server operation exists."""
    available = _methods(schema)
    missing = sorted(CODEX_REQUIRED_OPERATIONS - available)
    return {
        "provider": "codex-app-server",
        "provider_version": provider_version,
        "supported": not missing,
        "missing_operations": missing,
        "fallback": None,
    }


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
    if (not isinstance(created_at, int) or isinstance(created_at, bool) or created_at < 0
            or any(goal.get("threadId") != thread_id or goal.get("objective") != objective for goal in (created, observed))
            or observed.get("createdAt") != created_at):
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


def run_codex_adapter(
    rpc: Callable[[str, dict[str, Any]], dict[str, Any]], thread_id: str, objective: str,
    events: Iterable[dict[str, Any]], token_budget: int | None = None,
) -> dict[str, Any]:
    """Execute official app-server set/get/turn calls through an injected transport.

    The caller owns process lifecycle and captures only sanitised protocol facts.
    Clear is intentionally attempted after the read-back and its result is retained.
    """
    payload: dict[str, Any] = {"threadId": thread_id, "objective": objective, "status": "active"}
    if token_budget is not None:
        payload["tokenBudget"] = token_budget
    set_response = rpc("thread/goal/set", payload)
    turn_response = rpc("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": objective}]})
    turn = turn_response.get("turn") if isinstance(turn_response, dict) else None
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    get_response = rpc("thread/goal/get", {"threadId": thread_id})
    observation = observe_codex_goal(thread_id, objective, set_response, get_response, events, {turn_id} if isinstance(turn_id, str) else set())
    try:
        clear_response = rpc("thread/goal/clear", {"threadId": thread_id})
    except Exception as exc:
        return {**observation, "goal_cleared": False, "cleanup_error": type(exc).__name__}
    if not isinstance(clear_response, dict):
        return {**observation, "goal_cleared": False, "cleanup_error": "malformed_clear_response"}
    return {**observation, "goal_cleared": bool(clear_response.get("cleared")), "cleanup_error": None}


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


def preserve_assignment_outcomes(plan: Iterable[tuple[str, str]], records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialise every planned cell; missing runs are data, never silently omitted."""
    by_cell: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        by_cell.setdefault((record.get("task_id"), record.get("arm")), []).append(dict(record))
    outcomes = []
    for task_id, arm in plan:
        matching = by_cell.get((task_id, arm), [])
        outcomes.extend(matching)
        if not matching:
            outcomes.append({
            "task_id": task_id, "arm": arm, "outcome": "not_started", "reason": "missing_assignment_record",
            })
    return outcomes


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(rel).to_bytes(8, "big")); digest.update(rel)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
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
    with tempfile.NamedTemporaryFile(prefix=".worker-export-", suffix=".tar", dir=output_path.parent, delete=False) as temporary:
        archive = Path(temporary.name)
        result = subprocess.run(["git", "archive", "--format=tar", starting_commit], cwd=repo_root, stdout=temporary, stderr=subprocess.PIPE, check=False)
    try:
        if result.returncode != 0:
            raise RuntimeError("task export archive failed")
        shutil.unpack_archive(str(archive), str(output_path), format="tar")
        allowed: list[Path] = []
        for raw_path in paths:
            relative = Path(raw_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("worker allowlist path escapes export")
            target = output_path / relative
            if not target.exists() or target.is_symlink():
                raise ValueError("worker allowlist path absent from export")
            allowed.append(relative)
        for target in sorted((path for path in output_path.rglob("*") if path.is_file() or path.is_symlink()), reverse=True):
            relative = target.relative_to(output_path)
            if not any(relative == prefix or prefix in relative.parents for prefix in allowed):
                target.unlink()
        for directory in sorted((path for path in output_path.rglob("*") if path.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        return output_path
    finally:
        archive.unlink(missing_ok=True)


def worker_export_manifest(worker_root: Path) -> dict[str, Any]:
    return {"schema": "mission-worker-export/1", "sha256": _digest_tree(worker_root)}


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
