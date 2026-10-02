"""Closed verifier-policy loading for #878 acceptance imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class VerifierPolicyError(ValueError):
    pass


SCHEMA = "mission-verifier-policy/2"


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _text(value, code):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise VerifierPolicyError(code)
    return value


def _relative(value, code):
    value = _text(value, code)
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise VerifierPolicyError(code)
    if value.startswith("/") or "\\" in value or len(value) >= 2 and value[1] == ":" or any(part in {"", ".", ".."} for part in value.split("/")):
        if value != ".":
            raise VerifierPolicyError(code)
    return value


def validate(value):
    if not isinstance(value, dict) or set(value) != {"schema", "commands"}:
        raise VerifierPolicyError("verifier-policy-schema-invalid")
    if value["schema"] != SCHEMA:
        raise VerifierPolicyError("verifier-policy-unsupported")
    commands = value["commands"]
    if not isinstance(commands, list) or not commands:
        raise VerifierPolicyError("verifier-policy-commands-invalid")
    result = {}
    for command in commands:
        required = {"id", "argv", "relative_cwd", "timeout_sec", "output_limit", "kind", "env", "declared_untracked", "toolchain", "external_inputs"}
        optional = {"test_report", "replay"}
        if not isinstance(command, dict) or not required <= set(command) or not set(command) <= required | optional:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        identifier = _text(command["id"], "verifier-policy-command-id-invalid")
        argv = command["argv"]
        if identifier in result or not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item and "\x00" not in item for item in argv):
            raise VerifierPolicyError("verifier-policy-command-invalid")
        toolchain = command["toolchain"]
        if not isinstance(toolchain, dict) or set(toolchain) != {"path", "digest"} or not isinstance(toolchain["path"], str) or not toolchain["path"].startswith("/") or "\x00" in toolchain["path"] or not isinstance(toolchain["digest"], str) or __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", toolchain["digest"]) is None or argv[0] != toolchain["path"]:
            raise VerifierPolicyError("verifier-policy-toolchain-invalid")
        if command["kind"] not in {"command", "test"} or type(command["timeout_sec"]) is not int or not 0 < command["timeout_sec"] <= 3600 or type(command["output_limit"]) is not int or not 0 < command["output_limit"] <= 1048576:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        report = command.get("test_report")
        if command["kind"] == "test" and (not isinstance(report, dict) or set(report) != {"format", "path"}):
            raise VerifierPolicyError("verifier-policy-test-adapter-invalid")
        if command["kind"] == "test" and report["format"] != "junit-xml":
            raise VerifierPolicyError("verifier-policy-test-adapter-invalid")
        if command["kind"] == "test":
            _relative(report["path"], "verifier-policy-test-adapter-invalid")
        if command["kind"] == "command" and report is not None:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        inputs = command["external_inputs"]
        if not isinstance(inputs, list):
            raise VerifierPolicyError("verifier-policy-external-input-invalid")
        source_paths = set()
        target_paths = set()
        for item in inputs:
            if not isinstance(item, dict) or set(item) != {"kind", "source_path", "target_path"} or item["kind"] != "local-file":
                raise VerifierPolicyError("verifier-policy-external-input-unsupported")
            source = _relative(item["source_path"], "verifier-policy-external-input-invalid")
            target = _relative(item["target_path"], "verifier-policy-external-input-invalid")
            if source in source_paths or target in target_paths:
                raise VerifierPolicyError("verifier-policy-external-input-invalid")
            source_paths.add(source); target_paths.add(target)
        replay = command.get("replay")
        if replay is not None:
            if not isinstance(replay, dict) or set(replay) != {"command_id", "max_bytes", "allowed_artifact_kinds", "relative_path"} or not isinstance(replay["command_id"], str) or not replay["command_id"] or type(replay["max_bytes"]) is not int or not 0 < replay["max_bytes"] <= 1048576 or not isinstance(replay["allowed_artifact_kinds"], list) or not replay["allowed_artifact_kinds"] or not all(isinstance(item, str) and item for item in replay["allowed_artifact_kinds"]):
                raise VerifierPolicyError("verifier-policy-replay-invalid")
            _relative(replay["relative_path"], "verifier-policy-replay-invalid")
        _relative(command["relative_cwd"], "verifier-policy-cwd-invalid")
        if not isinstance(command["env"], dict) or not all(isinstance(key, str) and key and isinstance(item, str) for key, item in command["env"].items()):
            raise VerifierPolicyError("verifier-policy-env-invalid")
        _validate_explicit_paths(argv, command["env"])
        outputs = command["declared_untracked"]
        if not isinstance(outputs, list) or len(set(outputs)) != len(outputs):
            raise VerifierPolicyError("verifier-policy-output-invalid")
        for output in outputs:
            _relative(output, "verifier-policy-output-invalid")
        if command["kind"] == "test" and any(_paths_conflict(report["path"], path) for path in [*target_paths, *outputs]):
            raise VerifierPolicyError("verifier-policy-test-report-input-conflict")
        result[identifier] = {key: command[key] for key in sorted(command)}
    for command in result.values():
        replay = command.get("replay")
        if replay is None:
            continue
        target = result.get(replay["command_id"])
        if target is None:
            raise VerifierPolicyError("verifier-policy-replay-invalid")
        report = target.get("test_report")
        if isinstance(report, dict) and _paths_conflict(replay["relative_path"], report["path"]):
            raise VerifierPolicyError("verifier-policy-replay-report-conflict")
    return result


def explicit_paths_are_supported(argv, env):
    """Return whether command inputs contain only snapshot-bindable path shapes."""
    import os
    import re
    import shlex
    from urllib.parse import unquote, urlsplit

    def path_unsupported(candidate):
        path, separator, selector = candidate.partition("::")
        if separator and selector and path.endswith(".py") and ":" not in path and not path.startswith("/") and "\\" not in path and all(part not in {"", ".."} for part in path.split("/")):
            return False
        return bool(urlsplit(candidate).scheme) or candidate.startswith("/") or (len(candidate) >= 3 and candidate[0].isalpha() and candidate[1:3] in {":/", ":\\"}) or any(part == ".." for part in candidate.replace("\\", "/").split("/"))

    def unsupported(value, *, split_option_value=False):
        if not isinstance(value, str):
            return True
        candidate = unquote(value)
        values = [candidate]
        for part in candidate.split("="):
            values.append(part)
        if split_option_value and "=" in candidate:
            try:
                for token in shlex.split(candidate.split("=", 1)[1]):
                    values.append(token)
                    for part in token.split("="):
                        values.append(part)
                        values.extend(shlex.split(part))
            except ValueError:
                return True
        for raw_item in values:
            item = raw_item.strip().strip("'\"").strip()
            if item.startswith("@") and path_unsupported(item[1:].strip().strip("'\"").strip()):
                return True
            if item.startswith("-") and not item.startswith("--"):
                compact = item[1:]
                if "/" in compact or ".." in compact:
                    return True
            if path_unsupported(item):
                return True
        return False

    if not isinstance(argv, list) or not isinstance(env, dict) or not all(isinstance(value, str) for value in env.values()):
        return False

    def inline_python_source(index):
        executable = Path(argv[0]).name.lower() if isinstance(argv[0], str) else ""
        return index == 2 and argv[1] == "-c" and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable) is not None

    def pytest_option_value(values, index):
        return index > 0 and values[index - 1] in {"--override-ini", "-o"}

    for index, value in enumerate(argv[1:], start=1):
        if inline_python_source(index):
            continue
        if unsupported(value, split_option_value=isinstance(value, str) and (value.startswith("--") or pytest_option_value(argv, index))):
            return False
    for key, value in env.items():
        if key == "PYTEST_ADDOPTS":
            try:
                values = shlex.split(value)
            except ValueError:
                return False
            for index, item in enumerate(values):
                if unsupported(item, split_option_value=item.startswith("--") or pytest_option_value(values, index)):
                    return False
        elif any(unsupported(part) for part in value.split(os.pathsep)):
            return False
    return True


def _validate_explicit_paths(argv, env):
    """Reject path-shaped command inputs that the snapshot cannot bind."""
    if not explicit_paths_are_supported(argv, env):
        raise VerifierPolicyError("verifier-policy-explicit-path-unsupported")


def _paths_conflict(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


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
