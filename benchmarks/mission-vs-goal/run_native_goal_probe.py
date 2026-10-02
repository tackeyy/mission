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
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from pathlib import Path

from native_goal_benchmark import (
    NATIVE_SCHEMA, create_immutable_package, immutable_manifest, observe_claude_goal,
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
                    raise RuntimeError(f"app-server {method} failed: {message['error']}")
                return message.get("result", {})
            if isinstance(message.get("method"), str):
                # Store names and identity only; raw turn text is never an artifact.
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId"), "turnId": turn.get("id")}})
        raise TimeoutError(f"app-server assignment deadline expired during {method}")

    def wait_for_event(self, method: str) -> bool:
        while time.monotonic() < self.deadline:
            message = self._next_message()
            if message is None: break
            if isinstance(message.get("method"), str):
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId"), "turnId": turn.get("id")}})
                if message["method"] == method:
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
        thread_started = rpc.request("thread/start", {"cwd": str(worktree), "model": model, "permissions": permissions})
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
            matched = any(isinstance(entry, dict) and any(isinstance(skill, dict) and skill.get("path") == str(skill_path) for skill in entry.get("skills", [])) for entry in entries)
            if not matched:
                return {"native_goal_observed": False, "fidelity": "unverified", "outcome": "failed", "reason": "package_skill_unobserved", "observed_config": observed_config, "config_matches": config_matches}
            skill_input = [{"type": "skill", "name": "mission", "path": str(skill_path)}]
            state_started_ns = time.time_ns()
            rpc.request("turn/start", {"threadId": thread_id, "model": model, "effort": effort, "permissions": permissions, "input": [*skill_input, {"type": "text", "text": assignment}]})
            rpc.wait_for_event("turn/completed")
            state = _fresh_mission_state(worktree, state_started_ns)
            base = {"native_goal_observed": False, "package_delivery": "skill_input", "budget_enforcement": "unavailable", "observed_config": observed_config, "config_matches": config_matches}
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
            rpc.wait_for_event("turn/completed")
            get_response = rpc.request("thread/goal/get", {"threadId": thread_id})
            observation = observe_codex_goal(thread_id, assignment, set_response, get_response, rpc.events, {turn_id})
            if observation["goal_status"] in {"complete", "budgetLimited", "usageLimited"}:
                break
        assert observation is not None
        if observation["goal_status"] == "active":
            observation = {**observation, "fidelity": "verified", "outcome": "blocked", "reason": "assignment_turn_limit"}
        if not config_matches and observation["fidelity"] == "verified":
            observation = {**observation, "fidelity": "unverified", "reason": "execution_config_mismatch"}
        try:
            clear_response = rpc.request("thread/goal/clear", {"threadId": thread_id})
        except (OSError, RuntimeError, TimeoutError) as exc:
            return {**observation, "goal_cleared": False, "cleanup_error": type(exc).__name__, "observed_config": observed_config, "config_matches": config_matches, "budget_enforcement": "goal_native" if arm == "goal" else "unavailable", "package_delivery": "skill_input" if arm == "mission" else None, "stderr_bytes": getattr(rpc, "stderr_bytes", 0), "stderr_truncated": getattr(rpc, "stderr_truncated", False)}
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
    session_id = result.get("session_id") if isinstance(result, dict) else None
    state = _current_mission_state(worktree, session_id, started_ns)
    if state is not None:
        if state.get("passes") is True:
            return {"native_goal_observed": False, "fidelity": "verified", "outcome": "completed", "reason": None, "package_delivery": "claude_plugin_dir", "package_loaded": True, "mission_state": state}
        if isinstance(state.get("halt_reason"), str) and state["halt_reason"]:
            return {"native_goal_observed": False, "fidelity": "verified", "outcome": "blocked", "reason": "mission_halted", "package_delivery": "claude_plugin_dir", "package_loaded": True, "mission_state": state}
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


def _fresh_mission_state(worktree: Path, started_ns: int) -> dict | None:
    root = worktree / ".mission-state" / "sessions"
    if not root.is_dir():
        return None
    candidates = [path for path in root.glob("*.json") if path.stat().st_mtime_ns >= started_ns]
    if len(candidates) != 1:
        return None
    try:
        state = json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    return {key: state.get(key) for key in ("session_id", "passes", "halt_reason", "loop_active", "mission_id")}


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
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.token_budget is not None and args.token_budget <= 0:
        parser.error("--token-budget must be positive")
    if args.max_budget_usd is not None and args.max_budget_usd <= 0:
        parser.error("--max-budget-usd must be positive")
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
            package_prepared = (package_root / "plugins" / "mission" / "skills" / "mission" / "SKILL.md").is_file()
            if not manifest["task_snapshot"]["clean"]:
                observation = {"native_goal_observed": False, "fidelity": "not_applicable", "outcome": "failed", "reason": "task_snapshot_dirty"}
            elif not manifest["task_snapshot"]["matches"]:
                observation = {"native_goal_observed": False, "fidelity": "not_applicable", "outcome": "failed", "reason": "task_snapshot_mismatch"}
            else:
                try:
                    if args.host == "codex":
                        observation = probe_codex(worktree, args.objective, args.acceptance_criterion, args.timeout_seconds, args.token_budget, args.max_turns, args.model_id, args.effort, args.permissions, args.arm, package_root)
                        manifest["provider_version"] = _codex_version()
                    else:
                        observation = probe_claude(worktree, package_root, args.objective, args.acceptance_criterion, args.timeout_seconds, args.max_budget_usd, args.arm, args.model_id, args.effort, args.permissions)
                        version = subprocess.run(["claude", "--version"], text=True, capture_output=True, check=False)
                        manifest["provider_version"] = version.stdout.strip() if version.returncode == 0 else None
                except (OSError, RuntimeError, TimeoutError) as exc:
                    observation = {"native_goal_observed": False, "fidelity": "unverified", "outcome": "failed", "reason": "adapter_execution_failed", "error_type": type(exc).__name__}
            write_record(output, {"schema": NATIVE_SCHEMA, "run_id": output.stem, "task_id": args.task_id, "arm": label, "manifest": manifest, "package_prepared": package_prepared, **observation})
    except (OSError, RuntimeError, ValueError, shutil.ReadError) as exc:
        write_record(output, {"schema": NATIVE_SCHEMA, "run_id": output.stem, "task_id": args.task_id, "arm": label,
                              "manifest": {"schema": "native-goal-benchmark-manifest/1", "state": "unprepared"},
                              "package_prepared": False, "outcome": "failed", "fidelity": "not_applicable",
                              "reason": "package_prepare_failed", "error_type": type(exc).__name__})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
