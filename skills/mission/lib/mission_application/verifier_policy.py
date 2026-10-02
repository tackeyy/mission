"""Closed verifier-policy loading for #878 acceptance imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class VerifierPolicyError(ValueError):
    pass


SCHEMA = "mission-verifier-policy/1"


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _text(value, code):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise VerifierPolicyError(code)
    return value


def _relative(value, code):
    value = _text(value, code)
    if value.startswith("/") or "\\" in value or len(value) >= 2 and value[1] == ":" or any(part in {"", ".", ".."} for part in value.split("/")):
        if value != ".":
            raise VerifierPolicyError(code)
    return value


def validate(value):
    if not isinstance(value, dict) or set(value) != {"schema", "commands"} or value["schema"] != SCHEMA:
        raise VerifierPolicyError("verifier-policy-schema-invalid")
    commands = value["commands"]
    if not isinstance(commands, list) or not commands:
        raise VerifierPolicyError("verifier-policy-commands-invalid")
    result = {}
    for command in commands:
        required = {"id", "argv", "relative_cwd", "timeout_sec", "output_limit", "kind", "env", "declared_untracked"}
        optional = {"executed_count_pattern", "toolchain_path", "replay"}
        if not isinstance(command, dict) or not required <= set(command) or not set(command) <= required | optional:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        identifier = _text(command["id"], "verifier-policy-command-id-invalid")
        argv = command["argv"]
        if identifier in result or not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item and "\x00" not in item for item in argv):
            raise VerifierPolicyError("verifier-policy-command-invalid")
        if command["kind"] not in {"command", "test"} or type(command["timeout_sec"]) is not int or not 0 < command["timeout_sec"] <= 3600 or type(command["output_limit"]) is not int or not 0 < command["output_limit"] <= 1048576:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        pattern = command.get("executed_count_pattern")
        if command["kind"] == "test" and (not isinstance(pattern, str) or not pattern or len(pattern) > 512):
            raise VerifierPolicyError("verifier-policy-test-adapter-invalid")
        if command["kind"] == "command" and pattern is not None:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        toolchain = command.get("toolchain_path")
        if toolchain is not None and (not isinstance(toolchain, str) or not toolchain.startswith("/") or "\x00" in toolchain):
            raise VerifierPolicyError("verifier-policy-toolchain-invalid")
        replay = command.get("replay")
        if replay is not None:
            if not isinstance(replay, dict) or set(replay) != {"command_id", "max_bytes", "allowed_artifact_kinds", "relative_path"} or not isinstance(replay["command_id"], str) or not replay["command_id"] or type(replay["max_bytes"]) is not int or not 0 < replay["max_bytes"] <= 1048576 or not isinstance(replay["allowed_artifact_kinds"], list) or not replay["allowed_artifact_kinds"] or not all(isinstance(item, str) and item for item in replay["allowed_artifact_kinds"]):
                raise VerifierPolicyError("verifier-policy-replay-invalid")
            _relative(replay["relative_path"], "verifier-policy-replay-invalid")
        _relative(command["relative_cwd"], "verifier-policy-cwd-invalid")
        if not isinstance(command["env"], dict) or not all(isinstance(key, str) and key and isinstance(item, str) for key, item in command["env"].items()):
            raise VerifierPolicyError("verifier-policy-env-invalid")
        outputs = command["declared_untracked"]
        if not isinstance(outputs, list) or len(set(outputs)) != len(outputs):
            raise VerifierPolicyError("verifier-policy-output-invalid")
        for output in outputs:
            _relative(output, "verifier-policy-output-invalid")
        result[identifier] = {key: command[key] for key in sorted(command)}
    return result


def load(project_root, *, user_path=None):
    project = Path(project_root) / ".mission" / "verifiers.json"
    selected = project if project.is_file() else Path(user_path) if user_path is not None else Path.home() / ".config" / "mission" / "verifiers.json"
    try:
        if selected.is_symlink():
            raise OSError("policy symlink")
        raw = selected.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VerifierPolicyError("verifier-policy-unavailable") from exc
    return {"digest": _digest(raw), "commands": validate(value)}


def freeze(policy, *, command_ids, expected_digest):
    if not isinstance(policy, dict) or policy.get("digest") != expected_digest or not isinstance(policy.get("commands"), dict):
        raise VerifierPolicyError("verifier-policy-digest-mismatch")
    selected = {}
    pending = list(command_ids)
    while pending:
        identifier = pending.pop()
        if identifier in selected:
            continue
        command = policy["commands"].get(identifier)
        if command is None:
            raise VerifierPolicyError("verifier-policy-command-unregistered")
        selected[identifier] = command
        replay = command.get("replay")
        if replay is not None:
            pending.append(replay["command_id"])
    return {"digest": policy["digest"], "commands": selected}
