"""Closed verifier-policy loading for #878 acceptance imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


from verifier_command import VerifierPolicyError, _text, validate_command, validate_command_links


SCHEMA = "mission-verifier-policy/2"


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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
        validate_command(command)
        identifier = command["id"]
        if identifier in result:
            raise VerifierPolicyError("verifier-policy-command-invalid")
        _validate_explicit_paths(command["argv"], command["env"])
        result[identifier] = {key: command[key] for key in sorted(command)}
    validate_command_links(result)
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
        try:
            scheme = urlsplit(candidate).scheme
        except ValueError:
            # Malformed URL syntax is an unsupported command input too.
            # Keep this predicate total for both policy import and execution.
            return True
        return bool(scheme) or candidate.startswith("/") or (len(candidate) >= 3 and candidate[0].isalpha() and candidate[1:3] in {":/", ":\\"}) or any(part == ".." for part in candidate.replace("\\", "/").split("/"))

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
        identifier = _text(pending.pop(), "verifier-policy-command-invalid")
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
