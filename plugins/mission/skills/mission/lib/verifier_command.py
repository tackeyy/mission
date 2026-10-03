"""Pure, closed verifier command shapes shared by policy and completion gates."""
from __future__ import annotations


class VerifierPolicyError(ValueError):
    pass


def _text(value, code):
    if not isinstance(value, str) or not value or "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise VerifierPolicyError(code)
    return value


def _relative(value, code):
    value = _text(value, code)
    if value.startswith("/") or "\\" in value or len(value) >= 2 and value[1] == ":" or any(part in {"", ".", ".."} for part in value.split("/")):
        if value != ".":
            raise VerifierPolicyError(code)
    return value


def validate_command(command):
    """Validate every field and element before hashing, lookup, or capture."""
    required = {"id", "argv", "relative_cwd", "timeout_sec", "output_limit", "kind", "env", "declared_untracked", "toolchain", "external_inputs"}
    optional = {"test_report", "replay"}
    if not isinstance(command, dict) or not required <= set(command) or not set(command) <= required | optional:
        raise VerifierPolicyError("verifier-policy-command-invalid")
    _text(command["id"], "verifier-policy-command-id-invalid")
    argv = command["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item and "\x00" not in item for item in argv):
        raise VerifierPolicyError("verifier-policy-command-invalid")
    for argument in argv:
        _text(argument, "verifier-policy-command-invalid")
    toolchain = command["toolchain"]
    if not isinstance(toolchain, dict) or set(toolchain) != {"path", "digest"} or not isinstance(toolchain["path"], str) or not toolchain["path"].startswith("/") or "\x00" in toolchain["path"] or not isinstance(toolchain["digest"], str) or __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", toolchain["digest"]) is None or argv[0] != toolchain["path"]:
        raise VerifierPolicyError("verifier-policy-toolchain-invalid")
    _text(toolchain["path"], "verifier-policy-toolchain-invalid")
    if not isinstance(command["kind"], str) or command["kind"] not in {"command", "test"} or type(command["timeout_sec"]) is not int or not 0 < command["timeout_sec"] <= 3600 or type(command["output_limit"]) is not int or not 0 < command["output_limit"] <= 1048576:
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
        _text(replay["command_id"], "verifier-policy-replay-invalid")
        for kind in replay["allowed_artifact_kinds"]:
            _text(kind, "verifier-policy-replay-invalid")
        _relative(replay["relative_path"], "verifier-policy-replay-invalid")
    _relative(command["relative_cwd"], "verifier-policy-cwd-invalid")
    if not isinstance(command["env"], dict) or not all(isinstance(key, str) and key and isinstance(item, str) for key, item in command["env"].items()):
        raise VerifierPolicyError("verifier-policy-env-invalid")
    for key, item in command["env"].items():
        _text(key, "verifier-policy-env-invalid")
        if "=" in key or "\x00" in item or any(0xD800 <= ord(character) <= 0xDFFF for character in item):
            raise VerifierPolicyError("verifier-policy-env-invalid")
    outputs = command["declared_untracked"]
    if not isinstance(outputs, list):
        raise VerifierPolicyError("verifier-policy-output-invalid")
    for output in outputs:
        _relative(output, "verifier-policy-output-invalid")
    if len(set(outputs)) != len(outputs):
        raise VerifierPolicyError("verifier-policy-output-invalid")
    if command["kind"] == "test" and any(_paths_conflict(report["path"], path) for path in [*target_paths, *outputs]):
        raise VerifierPolicyError("verifier-policy-test-report-input-conflict")
    return command


def validate_command_links(commands):
    """Validate replay references after all command shapes have been closed."""
    for command in commands.values():
        replay = command.get("replay")
        if replay is None:
            continue
        target = commands.get(replay["command_id"])
        if target is None:
            raise VerifierPolicyError("verifier-policy-replay-invalid")
        report = target.get("test_report")
        if isinstance(report, dict) and _paths_conflict(replay["relative_path"], report["path"]):
            raise VerifierPolicyError("verifier-policy-replay-report-conflict")


def _paths_conflict(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")
