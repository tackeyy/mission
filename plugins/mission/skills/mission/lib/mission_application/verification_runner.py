"""Bounded actual-worktree snapshots for registered verification commands."""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import time
import selectors
import signal
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from mission_application.verifier_policy import explicit_paths_are_supported


class VerificationRunnerError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateFile:
    path: str
    mode: int
    content: bytes | None


@dataclass(frozen=True)
class CandidateSnapshot:
    files: tuple[CandidateFile, ...]
    digest: str


def _relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or any(0xD800 <= ord(character) <= 0xDFFF for character in path) or path.startswith("/") or "\\" in path or len(path) >= 2 and path[1] == ":" or any(part in {"", ".", ".."} for part in path.split("/")):
        raise VerificationRunnerError("candidate-path-invalid")
    return path


def _tracked(root: Path) -> list[tuple[str, int]]:
    result = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise VerificationRunnerError("candidate-git-unavailable")
    files = []
    seen = set()
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        left, raw_path = entry.split(b"\t", 1)
        fields = left.split()
        if len(fields) != 3 or fields[2] != b"0":
            raise VerificationRunnerError("candidate-git-conflict")
        mode = int(fields[0], 8)
        if mode == 0o160000:
            raise VerificationRunnerError("candidate-submodule-unsupported")
        path = _relative(raw_path.decode("utf-8"))
        if path in seen:
            raise VerificationRunnerError("candidate-git-conflict")
        seen.add(path)
        files.append((path, stat.S_IMODE(mode)))
    return files


def _target(root: Path, path: str) -> Path:
    """Resolve a relative candidate path without traversing a symlink."""
    path = _relative(path)
    target = root
    for part in path.split("/"):
        target = target / part
        try:
            if target.exists() and target.is_symlink():
                raise VerificationRunnerError("candidate-symlink")
        except OSError as exc:
            raise VerificationRunnerError("candidate-path-unavailable") from exc
    return target


def _read(root: Path, path: str, mode: int, *, required: bool, max_bytes: int | None = None) -> CandidateFile:
    target = _target(root, path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        if required:
            return CandidateFile(path, mode, None)
        raise VerificationRunnerError("declared-output-missing") from None
    except OSError as exc:
        raise VerificationRunnerError("candidate-read-invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise VerificationRunnerError("candidate-special-file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink) != (info.st_dev, info.st_ino, info.st_mode, info.st_nlink):
                raise VerificationRunnerError("candidate-changed-during-read")
            if max_bytes is not None and opened.st_size > max_bytes:
                raise VerificationRunnerError("candidate-read-too-large")
            content = handle.read()
            final = os.fstat(handle.fileno())
            if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                raise VerificationRunnerError("candidate-changed-during-read")
    except OSError as exc:
        raise VerificationRunnerError("candidate-read-invalid") from exc
    return CandidateFile(path, stat.S_IMODE(info.st_mode), content)


def _digest(files) -> str:
    digest = hashlib.sha256()
    for item in files:
        path = _relative(item.path)
        if type(item.mode) is not int or item.mode < 0 or item.mode > 0o7777 or item.content is not None and not isinstance(item.content, bytes):
            raise VerificationRunnerError("candidate-file-invalid")
        encoded = path.encode("utf-8")
        digest.update(b"path\0" + len(encoded).to_bytes(8, "big") + encoded)
        digest.update(b"mode\0" + item.mode.to_bytes(4, "big"))
        if item.content is None:
            digest.update(b"deleted\0")
        else:
            digest.update(b"content\0" + len(item.content).to_bytes(8, "big") + item.content)
    return "sha256:" + digest.hexdigest()


def _paths_conflict(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _validate_materialized_paths(files) -> None:
    paths = {_relative(item.path) for item in files}
    if len(paths) != len(files):
        raise VerificationRunnerError("candidate-path-conflict")
    for path in paths:
        parent = path
        while "/" in parent:
            parent = parent.rsplit("/", 1)[0]
            if parent in paths:
                raise VerificationRunnerError("candidate-path-conflict")


def capture_candidate(root, *, declared_untracked, external_inputs=()) -> CandidateSnapshot:
    root = Path(root).resolve()
    tracked = _tracked(root)
    names = {path for path, _mode in tracked}
    files = [_read(root, path, mode, required=True) for path, mode in tracked]
    declared = tuple(declared_untracked)
    if len(set(declared)) != len(declared):
        raise VerificationRunnerError("declared-output-duplicate")
    for path in declared:
        path = _relative(path)
        if path in names:
            raise VerificationRunnerError("declared-output-tracked")
        files.append(_read(root, path, 0, required=False))
    for item in external_inputs:
        if not isinstance(item, dict) or item.get("kind") != "local-file":
            raise VerificationRunnerError("external-input-invalid")
        source = _relative(item.get("source_path"))
        target = _relative(item.get("target_path"))
        if target in names or any(file.path == target for file in files):
            raise VerificationRunnerError("external-input-target-conflict")
        try:
            source_file = _read(root, source, 0, required=False)
        except VerificationRunnerError as exc:
            raise VerificationRunnerError("external-input-invalid") from exc
        files.append(CandidateFile(target, source_file.mode, source_file.content))
    files.sort(key=lambda item: item.path)
    _validate_materialized_paths(files)
    return CandidateSnapshot(tuple(files), _digest(files))


@contextlib.contextmanager
def materialize_candidate(candidate):
    if not isinstance(candidate, CandidateSnapshot) or candidate.digest != _digest(candidate.files):
        raise VerificationRunnerError("candidate-digest-invalid")
    _validate_materialized_paths(candidate.files)
    with tempfile.TemporaryDirectory(prefix="mission-verification-") as raw:
        root = Path(raw).resolve()
        for item in candidate.files:
            if item.content is None:
                continue
            target = _target(root, item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(root):
                raise VerificationRunnerError("candidate-path-invalid")
            target.write_bytes(item.content)
            os.chmod(target, item.mode)
        yield root


def verifier_definition_digest(command) -> str:
    """Return a stable identity for one frozen verifier definition."""
    import json

    raw = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _toolchain_matches(command) -> bool:
    toolchain = command.get("toolchain")
    if toolchain is None:
        return True
    if not isinstance(toolchain, dict):
        return False
    path = Path(toolchain.get("path", ""))
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == toolchain.get("digest")
    except OSError:
        return False


def _executed_count(command, root: Path) -> tuple[int | None, bool]:
    """Read a fresh, bounded JUnit report and verify its reported outcome."""
    if command.get("kind") != "test":
        return None, True
    report = command.get("test_report")
    if not isinstance(report, dict) or report.get("format") != "junit-xml":
        return None, False
    try:
        source = _read(root, _relative(report.get("path")), 0, required=False, max_bytes=1048576)
        if source.content is None:
            return None, False
        root_element = ET.fromstring(source.content)
    except (ET.ParseError, VerificationRunnerError, TypeError):
        return None, False
    if root_element.tag == "testsuite":
        suites = [root_element]
    elif root_element.tag == "testsuites" and all(child.tag == "testsuite" for child in root_element):
        suites = list(root_element)
    else:
        return None, False
    records = []
    stack = [(suite, None, 1) for suite in reversed(suites)]
    try:
        while stack:
            suite, parent, depth = stack.pop()
            if depth > 64 or len(records) >= 4096:
                return None, False
            record = {"suite": suite, "parent": parent, "tests": 0, "failures": 0, "errors": 0, "skipped": 0}
            index = len(records)
            records.append(record)
            for child in suite:
                if child.tag == "testcase":
                    if any(item.tag not in {"failure", "error", "skipped", "system-out", "system-err"} for item in child):
                        return None, False
                    record["tests"] += 1
                    record["failures"] += child.find("failure") is not None
                    record["errors"] += child.find("error") is not None
                    record["skipped"] += child.find("skipped") is not None
                    if record["tests"] > 65536:
                        return None, False
                elif child.tag == "testsuite":
                    stack.append((child, index, depth + 1))
                elif child.tag not in {"properties", "system-out", "system-err"}:
                    return None, False
        for record in reversed(records):
            if record["parent"] is not None:
                for name in ("tests", "failures", "errors", "skipped"):
                    records[record["parent"]][name] += record[name]
        for record in records:
            for name in ("tests", "failures", "errors", "skipped"):
                if name in record["suite"].attrib and int(record["suite"].attrib[name]) != record[name]:
                    return None, False
    except (ValueError, TypeError):
        return None, False
    root_stats = {name: sum(record[name] for record in records if record["parent"] is None) for name in ("tests", "failures", "errors", "skipped")}
    if root_stats["tests"] > 65536:
        return None, False
    if root_element.tag == "testsuites":
        try:
            for name in ("tests", "failures", "errors", "skipped"):
                if name in root_element.attrib and int(root_element.attrib[name]) != root_stats[name]:
                    return None, False
        except (ValueError, TypeError):
            return None, False
    return root_stats["tests"] - root_stats["skipped"], root_stats["failures"] == root_stats["errors"] == 0


def execute_candidate(candidate, command, *, relative_cwd, repro_input=None):
    """Run one frozen argv in a materialized candidate and return facts only.

    No caller supplied shell text or ambient environment reaches the child.
    ``output`` is retained only long enough to derive a digest/count; receipts
    deliberately do not persist process output.
    """
    if not isinstance(command, dict):
        raise VerificationRunnerError("verifier-definition-invalid")
    argv = command.get("argv")
    timeout = command.get("timeout_sec")
    limit = command.get("output_limit")
    if not isinstance(argv, list) or not argv or type(timeout) is not int or type(limit) is not int:
        raise VerificationRunnerError("verifier-definition-invalid")
    if not explicit_paths_are_supported(command["argv"], command.get("env", {})):
        raise VerificationRunnerError("verifier-explicit-path-unsupported")
    report_path = None
    if command.get("kind") == "test":
        report = command.get("test_report")
        if not isinstance(report, dict) or report.get("format") != "junit-xml":
            raise VerificationRunnerError("test-report-invalid")
        report_path = _relative(report.get("path"))
        if any(_paths_conflict(report_path, item.path) for item in candidate.files):
            raise VerificationRunnerError("test-report-input-conflict")
    before = candidate.digest
    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not _toolchain_matches(command):
        return {"started_at": started, "finished_at": started, "exit_code": None, "timed_out": False, "executed_count": None, "output_digest": "sha256:" + hashlib.sha256(b"").hexdigest(), "observed_output_bytes": 0, "output_truncated": False, "status": "blocked", "block_reason": "toolchain-stale", "repro_input_digest": None}
    timed_out = False
    with materialize_candidate(candidate) as root:
        repro_digest = None
        if repro_input is not None:
            artifact_kind, path, content = repro_input
            if not isinstance(artifact_kind, str) or not artifact_kind:
                raise VerificationRunnerError("repro-input-invalid")
            path = _relative(path)
            if not isinstance(content, bytes):
                raise VerificationRunnerError("repro-input-invalid")
            if any(_paths_conflict(path, item.path) for item in candidate.files):
                raise VerificationRunnerError("repro-input-path-conflict")
            if report_path is not None and _paths_conflict(path, report_path):
                raise VerificationRunnerError("test-report-input-conflict")
            target = _target(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            repro_digest = "sha256:" + hashlib.sha256(artifact_kind.encode() + b"\0" + path.encode() + b"\0" + content).hexdigest()
        cwd = root if relative_cwd == "." else root / _relative(relative_cwd)
        if not cwd.is_dir():
            raise VerificationRunnerError("verifier-cwd-missing")
        try:
            child = subprocess.Popen(
                argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            env={"PATH": str(Path(command["toolchain"]["path"]).parent) if command.get("toolchain") else os.defpath, **command.get("env", {})},
            )
        except OSError:
            return {
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "exit_code": None,
                "timed_out": False,
                "executed_count": None,
                "output_digest": "sha256:" + hashlib.sha256(b"").hexdigest(),
                "observed_output_bytes": 0,
                "output_truncated": False,
                "status": "blocked",
                "block_reason": "process-unavailable",
                "repro_input_digest": repro_digest,
            }
        output = bytearray()
        output_hash = hashlib.sha256()
        observed_output_bytes = 0
        output_truncated = False
        selector = selectors.DefaultSelector()
        assert child.stdout is not None
        stdout = child.stdout
        selector.register(stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while selector.get_map() or child.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if not timed_out:
                    timed_out = True
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    selector.close()
                    stdout.close()
                    break
                remaining = 0.1
            for key, _event in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    output_hash.update(chunk)
                    observed_output_bytes += len(chunk)
                    if len(output) < limit:
                        output.extend(chunk[:limit - len(output)])
                    if len(output) < observed_output_bytes:
                        output_truncated = True
        try:
            child.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                pass
            exit_code = child.poll()
        else:
            exit_code = child.returncode
        selector.close()
        stdout.close()
        count, report_successful = _executed_count(command, root)
        try:
            observed = tuple(_read(root, item.path, item.mode, required=True) for item in candidate.files)
            candidate_stale = _digest(observed) != candidate.digest
            observation_reason = "candidate-stale" if candidate_stale else None
        except (VerificationRunnerError, OSError):
            candidate_stale = True
            observation_reason = "candidate-observation-invalid"
    output_truncated = output_truncated or timed_out
    after = candidate.digest
    if before != after:
        raise VerificationRunnerError("candidate-mutated")
    toolchain_stale = not _toolchain_matches(command)
    passed = not timed_out and not candidate_stale and not toolchain_stale and exit_code == 0 and (command.get("kind") != "test" or (count is not None and count > 0 and report_successful))
    return {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "executed_count": count,
        "output_digest": "sha256:" + output_hash.hexdigest(),
        "observed_output_bytes": observed_output_bytes,
        "output_truncated": output_truncated,
        "status": "passed" if passed else "blocked" if timed_out or candidate_stale or toolchain_stale else "failed",
        "block_reason": "timeout" if timed_out else observation_reason if candidate_stale else "toolchain-stale" if toolchain_stale else None,
        "repro_input_digest": repro_digest,
    }
