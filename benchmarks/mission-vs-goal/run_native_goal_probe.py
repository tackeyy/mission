#!/usr/bin/env python3
"""Run one bounded native Goal probe and emit a sanitised #882 record.

This is deliberately a probe, not an aggregate quality claim.  It starts the
published host interfaces, records unknown/unsupported outcomes faithfully, and
does not create credentials or alter host settings.
"""

from __future__ import annotations

import argparse
import json
import os
import math
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from pathlib import Path

from native_goal_benchmark import (
    NATIVE_SCHEMA, create_immutable_package, create_worker_export, immutable_manifest, initialize_worker_export_repository, observe_claude_goal, worker_export_manifest,
    observe_codex_goal, write_record,
)


class RpcProcess:
    """Small JSON-RPC client for the public ``codex app-server --stdio`` entrypoint."""

    def __init__(self, command: list[str], timeout: float):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.sequence = 0
        self.events: list[dict] = []
        self._buffer = bytearray()
        self.stderr_bytes = 0
        self.stderr_truncated = False
        self.selector = selectors.DefaultSelector()
        assert self.process.stdout is not None
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        while chunk := self.process.stderr.read(65536):
            self.stderr_bytes += len(chunk)
            if self.stderr_bytes > 65536:
                self.stderr_truncated = True

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        while time.monotonic() < self.deadline:
            message = self._next_message()
            if message is None: break
            if message.get("id") == request_id:
                if "error" in message:
                    error = message["error"] if isinstance(message["error"], dict) else {}
                    raise RpcProtocolError(method, error.get("code"), error.get("message"))
                return message.get("result", {})
            if isinstance(message.get("method"), str):
                # Store names and identity only; raw turn text is never an artifact.
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId"), "turnId": turn.get("id")}})
        raise TimeoutError(f"app-server assignment deadline expired during {method}")

    def wait_for_event(self, method: str, thread_id: str | None = None, turn_id: str | None = None) -> bool:
        while time.monotonic() < self.deadline:
            message = self._next_message()
            if message is None: break
            if isinstance(message.get("method"), str):
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId"), "turnId": turn.get("id")}})
                if (message["method"] == method
                        and (thread_id is None or params.get("threadId") == thread_id)
                        and (turn_id is None or turn.get("id") == turn_id)):
                    return True
        return False

    def _next_message(self) -> dict | None:
        """Read one bounded newline-delimited JSON message without blocking on a partial line."""
        while b"\n" not in self._buffer:
            ready = self.selector.select(max(0, self.deadline - time.monotonic()))
            if not ready:
                return None
            assert self.process.stdout is not None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                if self._buffer:
                    raise RuntimeError("malformed_jsonrpc")
                return None
            self._buffer.extend(chunk)
            if len(self._buffer) > 1_048_576:
                raise RuntimeError("jsonrpc_message_too_large")
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("malformed_jsonrpc") from exc
        if not isinstance(value, dict):
            raise RuntimeError("malformed_jsonrpc")
        return value

    def close(self) -> None:
        self.selector.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self._stderr_thread.join(timeout=1)


def _thread_id(response: dict) -> str:
    thread = response.get("thread") if isinstance(response, dict) else None
    value = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(value, str) or not value:
        raise RuntimeError("thread/start did not return a thread id")
    return value


class RpcProtocolError(RuntimeError):
    def __init__(self, method: str, code: object, message: object):
        self.method, self.code = method, code
        super().__init__(f"app-server {method} failed: {message}")


def _unsupported_goal_protocol(exc: Exception) -> bool:
    """Recognise the official JSON-RPC missing-method response without fallback."""
    return isinstance(exc, RpcProtocolError) and exc.method.startswith("thread/goal/") and exc.code == -32601


def _codex_version() -> str:
    result = subprocess.run(["codex", "--version"], text=True, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("provider_version_unavailable")
    return result.stdout.strip()


def probe_codex(worktree: Path, objective: str, acceptance: str, timeout: float, token_budget: int | None, max_turns: int,
                model: str, effort: str, permissions: str, arm: str = "goal", package_root: Path | None = None) -> dict:
    rpc = RpcProcess(["codex", "app-server", "--stdio"], timeout)
    observation: dict | None = None
    try:
        rpc.request("initialize", {"clientInfo": {"name": "mission-native-goal-benchmark", "version": "1"}, "capabilities": {"experimentalApi": True}})
        thread_started = rpc.request("thread/start", {"cwd": str(worktree), "model": model, "permissions": permissions, "config": {"model_reasoning_effort": effort}})
        thread_id = _thread_id(thread_started)
        observed_config = {key: thread_started.get(key) for key in ("model", "modelProvider", "reasoningEffort", "activePermissionProfile", "sandbox")}
        profile = observed_config["activePermissionProfile"]
        profile_id = profile.get("id") if isinstance(profile, dict) else None
        config_matches = observed_config["model"] == model and observed_config["reasoningEffort"] == effort and profile_id == permissions
        assignment = f"{objective}\n\nAcceptance criterion: {acceptance}"
        skill_input: list[dict] = []
        if arm == "mission":
            if package_root is None:
                raise RuntimeError("package_root_required")
            skill_path = package_root / "skills" / "mission" / "SKILL.md"
            rpc.request("skills/extraRoots/set", {"extraRoots": [str(package_root / "skills")]})
            listed = rpc.request("skills/list", {"cwds": [str(worktree)], "forceReload": True})
            entries = listed.get("data", []) if isinstance(listed, dict) else []
            expected = skill_path.resolve()
            def is_expected_skill(skill: object) -> bool:
                if not isinstance(skill, dict) or not isinstance(skill.get("path"), str):
                    return False
                try:
                    candidate = Path(skill["path"]).resolve()
                    return candidate == expected and candidate.is_file() and candidate.samefile(expected)
                except OSError:
                    return False
            matched = any(isinstance(entry, dict) and any(is_expected_skill(skill) for skill in entry.get("skills", [])) for entry in entries)
            if not matched:
                return {"native_goal_observed": False, "fidelity": "unverified", "outcome": "failed", "reason": "package_skill_unobserved", "observed_config": observed_config, "config_matches": config_matches}
            skill_input = [{"type": "skill", "name": "mission", "path": str(skill_path)}]
            state_started_ns = time.time_ns()
            started = rpc.request("turn/start", {"threadId": thread_id, "model": model, "effort": effort, "permissions": permissions, "input": [*skill_input, {"type": "text", "text": assignment}]})
            turn = started.get("turn") if isinstance(started, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            completed = isinstance(turn_id, str) and bool(turn_id) and rpc.wait_for_event("turn/completed", thread_id, turn_id)
            state = _fresh_mission_state(worktree, state_started_ns, thread_id) if completed else None
            base = {"native_goal_observed": False, "package_delivery": "skill_input", "budget_enforcement": "unavailable", "observed_config": observed_config, "config_matches": config_matches}
            if not completed:
                return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "turn_completion_unobserved", "mission_state": None}
            if state and state.get("passes") is True and config_matches:
                return {**base, "fidelity": "verified", "outcome": "completed", "reason": None, "mission_state": state}
            if state and isinstance(state.get("halt_reason"), str) and state["halt_reason"] and config_matches:
                return {**base, "fidelity": "verified", "outcome": "blocked", "reason": "mission_halted", "mission_state": state}
            return {**base, "fidelity": "unverified", "outcome": "failed", "reason": "mission_state_unobserved" if state is None else "execution_config_mismatch", "mission_state": state}
        goal = {"threadId": thread_id, "objective": assignment, "status": "active"}
        if token_budget is not None:
            goal["tokenBudget"] = token_budget
        set_response = rpc.request("thread/goal/set", goal)
        get_response: dict = {}
        for _ in range(max_turns):
            turn_started = rpc.request("turn/start", {"threadId": thread_id, "model": model, "effort": effort, "permissions": permissions, "input": [*skill_input, {"type": "text", "text": assignment}]})
            turn = turn_started.get("turn") if isinstance(turn_started, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise RuntimeError("turn_identity_unobserved")
            completed = rpc.wait_for_event("turn/completed", thread_id, turn_id)
            get_response = rpc.request("thread/goal/get", {"threadId": thread_id})
            observation = observe_codex_goal(thread_id, assignment, set_response, get_response, rpc.events, {turn_id})
            if not completed:
                observation = {**observation, "fidelity": "unverified", "outcome": "failed", "reason": "turn_completion_unobserved"}
                break
            if observation["goal_status"] in {"complete", "budgetLimited", "usageLimited"}:
                break
        assert observation is not None
        if observation["goal_status"] == "active":
            observation = {**observation, "outcome": "blocked", "reason": "assignment_turn_limit"}
        if not config_matches and observation["fidelity"] == "verified":
            observation = {**observation, "fidelity": "unverified", "reason": "execution_config_mismatch"}
        try:
            clear_response = rpc.request("thread/goal/clear", {"threadId": thread_id})
        except (OSError, RuntimeError, TimeoutError) as exc:
            return {**observation, "goal_cleared": False, "cleanup_error": type(exc).__name__, "observed_config": observed_config, "config_matches": config_matches, "budget_enforcement": "goal_native" if arm == "goal" else "unavailable", "package_delivery": "skill_input" if arm == "mission" else None, "stderr_bytes": getattr(rpc, "stderr_bytes", 0), "stderr_truncated": getattr(rpc, "stderr_truncated", False)}
        if not isinstance(clear_response, dict):
            return {**observation, "goal_cleared": False, "cleanup_error": "malformed_clear_response", "observed_config": observed_config, "config_matches": config_matches, "budget_enforcement": "goal_native", "package_delivery": None, "stderr_bytes": getattr(rpc, "stderr_bytes", 0), "stderr_truncated": getattr(rpc, "stderr_truncated", False)}
        return {**observation, "goal_cleared": bool(clear_response.get("cleared")), "cleanup_error": None, "observed_config": observed_config, "config_matches": config_matches, "budget_enforcement": "goal_native" if arm == "goal" else "unavailable", "package_delivery": "skill_input" if arm == "mission" else None, "stderr_bytes": getattr(rpc, "stderr_bytes", 0), "stderr_truncated": getattr(rpc, "stderr_truncated", False)}
    finally:
        rpc.close()


def probe_claude(worktree: Path, package_root: Path, objective: str, acceptance: str, timeout: float, max_budget_usd: float | None, arm: str = "goal", model: str | None = None, effort: str | None = None, permissions: str | None = None) -> dict:
    command_name = "/goal" if arm == "goal" else "/mission"
    invocation = f"{command_name} {objective}\n\nAcceptance criterion: {acceptance}"
    command = ["claude", "--plugin-dir", str(package_root / "plugins" / "mission"), "--print", "--output-format", "json", invocation]
    if model: command[1:1] = ["--model", model]
    if effort: command[1:1] = ["--effort", effort]
    if permissions: command[1:1] = ["--permission-mode", permissions]
    if max_budget_usd is not None:
        command[1:1] = ["--max-budget-usd", str(max_budget_usd)]
    started_ns = time.time_ns()
    try:
        completed = subprocess.run(command, cwd=worktree, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"native_goal_observed": True, "fidelity": "unverified", "outcome": "blocked", "reason": "timeout"}
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    if arm == "goal":
        return observe_claude_goal(invocation, completed.returncode, result)
    # Print JSON exposes model usage but not an authoritative effort or permission
    # read-back.  A Mission completion is therefore not comparable until all three
    # configured values can be observed from a public response.
    model_usage = result.get("modelUsage") if isinstance(result, dict) else None
    observed_model = next(iter(model_usage), None) if isinstance(model_usage, dict) else None
    config_matches = False
    session_id = result.get("session_id") if isinstance(result, dict) else None
    state = _current_mission_state(worktree, session_id, started_ns)
    if state is not None:
        if state.get("passes") is True or (isinstance(state.get("halt_reason"), str) and state["halt_reason"]):
            return {"native_goal_observed": False, "fidelity": "unverified", "outcome": "failed", "reason": "execution_config_unobserved", "package_delivery": "claude_plugin_dir", "package_loaded": True, "mission_state": state, "observed_model": observed_model, "config_matches": config_matches}
    return {
        "native_goal_observed": False,
        "fidelity": "not_applicable",
        "outcome": "failed",
        "reason": "mission_state_unobserved",
        "package_delivery": "claude_plugin_dir",
        "package_loaded": None,
    }


def _current_mission_state(worktree: Path, session_id: object, started_ns: int) -> dict | None:
    """Read only a state generated by this Claude session after this assignment started."""
    if not isinstance(session_id, str) or not session_id:
        return None
    root = worktree / ".mission-state"
    paths = list((root / "sessions").glob("*.json")) if (root / "sessions").is_dir() else []
    legacy = root / "state.json"
    if legacy.is_file(): paths.append(legacy)
    for path in sorted(paths, key=lambda item: item.stat().st_mtime_ns, reverse=True):
        if path.stat().st_mtime_ns < started_ns or session_id not in path.name:
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(state, dict) and state.get("session_id") in {session_id, f"cc-{session_id}"}:
            return {key: state.get(key) for key in ("session_id", "passes", "halt_reason", "loop_active", "mission_id")}
    return None


def _fresh_mission_state(worktree: Path, started_ns: int, thread_id: str) -> dict | None:
    root = worktree / ".mission-state" / "sessions"
    if not root.is_dir():
        return None
    expected_session_id = f"cx-{thread_id}"
    for candidate in root.glob("*.json"):
        try:
            if candidate.stat().st_mtime_ns < started_ns:
                continue
            state = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # mission-state derives Codex session_id from CODEX_THREAD_ID as
        # ``cx-<thread id>``. A fresh file alone can be another assignment, so
        # bind the state and its filename to the started host thread. The
        # terminal event itself is separately bound by wait_for_event.
        if (not isinstance(state, dict) or state.get("session_id") != expected_session_id
                or candidate.stem != expected_session_id):
            continue
        return {key: state.get(key) for key in ("session_id", "passes", "halt_reason", "loop_active", "mission_id")}
    return None


def _commit_at(worktree: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, text=True, capture_output=True, check=False)
    commit = result.stdout.strip()
    if result.returncode != 0 or len(commit) != 40:
        raise RuntimeError("task_snapshot_unavailable")
    return commit


def _task_snapshot(worktree: Path) -> dict:
    commit = _commit_at(worktree)
    status = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree, text=True, capture_output=True, check=False)
    if status.returncode != 0:
        raise RuntimeError("task_snapshot_unavailable")
    return {"observed": commit, "clean": not bool(status.stdout.strip())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("codex", "claude"), required=True)
    parser.add_argument("--arm", choices=("goal", "mission"), default="goal")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--acceptance-criterion", required=True)
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--mission-source-commit", required=True)
    parser.add_argument("--mission-source-repo", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--permissions", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--token-budget", type=int, default=None)
    parser.add_argument("--max-budget-usd", type=float, default=None)
    parser.add_argument("--max-turns", type=int, default=2)
    parser.add_argument("--worker-allow-path", action="append", default=[],
                        help="repo-relative task-contract path retained in the immutable worker export; repeat for each path")
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and positive")
    if args.token_budget is not None and args.token_budget <= 0:
        parser.error("--token-budget must be positive")
    if args.max_budget_usd is not None and (not math.isfinite(args.max_budget_usd) or args.max_budget_usd <= 0):
        parser.error("--max-budget-usd must be finite and positive")
    if args.max_turns <= 0:
        parser.error("--max-turns must be positive")
    worktree = Path(args.worktree).resolve()
    mission_source_repo = Path(args.mission_source_repo).resolve()
    output = Path(args.output).resolve()
    label = "codex_native_goal" if args.host == "codex" and args.arm == "goal" else ("claude_code_native_goal" if args.arm == "goal" else "mission")
    try:
        actual_snapshot = _task_snapshot(worktree)
        with tempfile.TemporaryDirectory(prefix="mission-native-goal-package-") as temporary:
            package = Path(temporary) / "mission.tar"
            create_immutable_package(mission_source_repo, args.mission_source_commit, package)
            package_root = Path(temporary) / "package"
            shutil.unpack_archive(str(package), str(package_root), format="tar")
            manifest = immutable_manifest(args.mission_source_commit, package_root, package, {
                "host": args.host, "arm": args.arm, "model_id": args.model_id,
                "effort": args.effort, "permissions": args.permissions, "objective": args.objective,
                "acceptance_criterion": args.acceptance_criterion, "timeout_seconds": args.timeout_seconds,
                "token_budget": args.token_budget, "max_budget_usd": args.max_budget_usd, "max_turns": args.max_turns,
            })
            manifest["task_snapshot"] = {"expected": args.starting_commit, **actual_snapshot, "matches": actual_snapshot["observed"] == args.starting_commit and actual_snapshot["clean"]}
            manifest["mission_source_commit"] = args.mission_source_commit
            candidate_root = output.parent / "candidates" / output.stem
            worker_root = create_worker_export(worktree, args.starting_commit, candidate_root, args.worker_allow_path)
            export_commit = initialize_worker_export_repository(worker_root)
            manifest["worker_export"] = {
                "source_commit": args.starting_commit,
                "export_commit": export_commit,
                "allowlist_count": len(args.worker_allow_path),
                "candidate_path": str(candidate_root.relative_to(output.parent)),
                "initial_sha256": worker_export_manifest(worker_root)["sha256"],
            }
            package_prepared = (package_root / "plugins" / "mission" / "skills" / "mission" / "SKILL.md").is_file()
            if not manifest["task_snapshot"]["clean"]:
                observation = {"native_goal_observed": False, "fidelity": "not_applicable", "outcome": "failed", "reason": "task_snapshot_dirty"}
            elif not manifest["task_snapshot"]["matches"]:
                observation = {"native_goal_observed": False, "fidelity": "not_applicable", "outcome": "failed", "reason": "task_snapshot_mismatch"}
            else:
                try:
                    if args.host == "codex":
                        observation = probe_codex(worker_root, args.objective, args.acceptance_criterion, args.timeout_seconds, args.token_budget, args.max_turns, args.model_id, args.effort, args.permissions, args.arm, package_root)
                        manifest["provider_version"] = _codex_version()
                    else:
                        observation = probe_claude(worker_root, package_root, args.objective, args.acceptance_criterion, args.timeout_seconds, args.max_budget_usd, args.arm, args.model_id, args.effort, args.permissions)
                        version = subprocess.run(["claude", "--version"], text=True, capture_output=True, check=False)
                        manifest["provider_version"] = version.stdout.strip() if version.returncode == 0 else None
                except (OSError, RuntimeError, TimeoutError) as exc:
                    if args.host == "codex" and args.arm == "goal" and _unsupported_goal_protocol(exc):
                        observation = {"native_goal_observed": False, "fidelity": "not_applicable", "outcome": "unsupported", "reason": "native_goal_protocol_unavailable", "error_type": type(exc).__name__}
                    else:
                        observation = {"native_goal_observed": False, "fidelity": "unverified", "outcome": "failed", "reason": "adapter_execution_failed", "error_type": type(exc).__name__}
            try:
                manifest["worker_export"]["candidate_sha256"] = worker_export_manifest(worker_root)["sha256"]
            except ValueError:
                manifest["worker_export"]["candidate_state"] = "stale"
                observation = {**observation, "fidelity": "unverified", "outcome": "failed", "reason": "candidate_snapshot_invalid"}
            write_record(output, {"schema": NATIVE_SCHEMA, "run_id": output.stem, "task_id": args.task_id, "arm": label, "manifest": manifest, "package_prepared": package_prepared, **observation})
    except (OSError, RuntimeError, ValueError, shutil.ReadError) as exc:
        write_record(output, {"schema": NATIVE_SCHEMA, "run_id": output.stem, "task_id": args.task_id, "arm": label,
                              "manifest": {"schema": "native-goal-benchmark-manifest/1", "state": "unprepared"},
                              "package_prepared": False, "outcome": "failed", "fidelity": "not_applicable",
                              "reason": "package_prepare_failed", "error_type": type(exc).__name__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
