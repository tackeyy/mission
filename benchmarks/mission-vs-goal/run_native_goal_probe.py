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
from pathlib import Path

from native_goal_benchmark import (
    NATIVE_SCHEMA, create_immutable_package, immutable_manifest, observe_claude_goal,
    observe_codex_goal, write_record,
)


class RpcProcess:
    """Small JSON-RPC client for the public ``codex app-server --stdio`` entrypoint."""

    def __init__(self, command: list[str], timeout: float):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        self.timeout = timeout
        self.sequence = 0
        self.events: list[dict] = []
        self.selector = selectors.DefaultSelector()
        assert self.process.stdout is not None
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            ready = self.selector.select(max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"app-server {method} failed: {message['error']}")
                return message.get("result", {})
            if isinstance(message.get("method"), str):
                # Store names and identity only; raw turn text is never an artifact.
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId")}})
        raise TimeoutError(f"app-server {method} did not respond within {self.timeout} seconds")

    def wait_for_event(self, method: str) -> bool:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            ready = self.selector.select(max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            if isinstance(message.get("method"), str):
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                self.events.append({"method": message["method"], "params": {"threadId": params.get("threadId")}})
                if message["method"] == method:
                    return True
        return False

    def close(self) -> None:
        self.selector.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()


def _thread_id(response: dict) -> str:
    thread = response.get("thread") if isinstance(response, dict) else None
    value = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(value, str) or not value:
        raise RuntimeError("thread/start did not return a thread id")
    return value


def probe_codex(worktree: Path, objective: str, timeout: float, token_budget: int | None) -> dict:
    rpc = RpcProcess(["codex", "app-server", "--stdio"], timeout)
    try:
        rpc.request("initialize", {"clientInfo": {"name": "mission-native-goal-benchmark", "version": "1"}, "capabilities": {}})
        thread_id = _thread_id(rpc.request("thread/start", {"cwd": str(worktree)}))
        goal = {"threadId": thread_id, "objective": objective, "status": "active"}
        if token_budget is not None:
            goal["tokenBudget"] = token_budget
        set_response = rpc.request("thread/goal/set", goal)
        rpc.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": objective}]})
        rpc.wait_for_event("turn/completed")
        get_response = rpc.request("thread/goal/get", {"threadId": thread_id})
        observation = observe_codex_goal(thread_id, objective, set_response, get_response, rpc.events)
        clear_response = rpc.request("thread/goal/clear", {"threadId": thread_id})
        return {**observation, "goal_cleared": bool(clear_response.get("cleared"))}
    finally:
        rpc.close()


def probe_claude(worktree: Path, package_root: Path, objective: str, acceptance: str, timeout: float, max_budget_usd: float | None, arm: str = "goal") -> dict:
    command_name = "/goal" if arm == "goal" else "/mission"
    invocation = f"{command_name} {objective}\n\nAcceptance criterion: {acceptance}"
    command = ["claude", "--plugin-dir", str(package_root / "plugins" / "mission"), "--print", "--output-format", "json", invocation]
    if max_budget_usd is not None:
        command[1:1] = ["--max-budget-usd", str(max_budget_usd)]
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
    terminal = isinstance(result, dict) and isinstance(result.get("session_id"), str) and result.get("is_error") is False
    return {
        "native_goal_observed": False,
        "fidelity": "not_applicable",
        "outcome": "completed" if completed.returncode == 0 and terminal else "failed",
        "reason": None if completed.returncode == 0 and terminal else "terminal_mission_observation_missing",
        "package_delivery": "claude_plugin_dir",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("codex", "claude"), required=True)
    parser.add_argument("--arm", choices=("goal", "mission"), default="goal")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--acceptance-criterion", required=True)
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--token-budget", type=int, default=None)
    parser.add_argument("--max-budget-usd", type=float, default=None)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.token_budget is not None and args.token_budget <= 0:
        parser.error("--token-budget must be positive")
    if args.max_budget_usd is not None and args.max_budget_usd <= 0:
        parser.error("--max-budget-usd must be positive")
    if args.host == "codex" and args.arm != "goal":
        parser.error("Codex mission package execution is not supported by this public app-server adapter")
    worktree = Path(args.worktree).resolve()
    output = Path(args.output).resolve()
    with tempfile.TemporaryDirectory(prefix="mission-native-goal-package-") as temporary:
        package = Path(temporary) / "mission.tar"
        create_immutable_package(worktree, args.starting_commit, package)
        package_root = Path(temporary) / "package"
        shutil.unpack_archive(str(package), str(package_root), format="tar")
        manifest = immutable_manifest(args.starting_commit, package_root, package, {
            "host": args.host, "arm": args.arm, "timeout_seconds": args.timeout_seconds, "worktree_snapshot": args.starting_commit,
            "task_id": args.task_id, "acceptance_criterion": args.acceptance_criterion,
            "token_budget": args.token_budget, "max_budget_usd": args.max_budget_usd,
        })
        package_prepared = (package_root / "plugins" / "mission" / "skills" / "mission" / "SKILL.md").is_file()
        try:
            observation = probe_codex(worktree, args.objective, args.timeout_seconds, args.token_budget) if args.host == "codex" else probe_claude(worktree, package_root, args.objective, args.acceptance_criterion, args.timeout_seconds, args.max_budget_usd, args.arm)
        except (OSError, RuntimeError, TimeoutError) as exc:
            observation = {"native_goal_observed": False, "fidelity": "unverified", "outcome": "unsupported", "reason": type(exc).__name__}
        label = f"{args.host}_native_goal" if args.arm == "goal" else "mission"
        write_record(output, {"schema": NATIVE_SCHEMA, "run_id": output.stem, "task_id": args.task_id, "arm": label, "manifest": manifest, "package_prepared": package_prepared, **observation})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
