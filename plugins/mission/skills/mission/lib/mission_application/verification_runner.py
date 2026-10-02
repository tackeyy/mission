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
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path


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
    if not isinstance(path, str) or not path or "\x00" in path or path.startswith("/") or "\\" in path or len(path) >= 2 and path[1] == ":" or any(part in {"", ".", ".."} for part in path.split("/")):
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
        if target.exists() and target.is_symlink():
            raise VerificationRunnerError("candidate-symlink")
    return target


def _read(root: Path, path: str, mode: int, *, required: bool) -> CandidateFile:
    target = _target(root, path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        if required:
            return CandidateFile(path, mode, None)
        raise VerificationRunnerError("declared-output-missing") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise VerificationRunnerError("candidate-special-file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink) != (info.st_dev, info.st_ino, info.st_mode, info.st_nlink):
                raise VerificationRunnerError("candidate-changed-during-read")
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


def capture_candidate(root, *, declared_untracked) -> CandidateSnapshot:
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
    files.sort(key=lambda item: item.path)
    return CandidateSnapshot(tuple(files), _digest(files))


@contextlib.contextmanager
def materialize_candidate(candidate):
    if not isinstance(candidate, CandidateSnapshot) or len({item.path for item in candidate.files}) != len(candidate.files) or candidate.digest != _digest(candidate.files):
        raise VerificationRunnerError("candidate-digest-invalid")
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


def _executed_count(command, output: bytes) -> int | None:
    """Count only a policy-declared, machine-readable test adapter result."""
    pattern = command.get("executed_count_pattern")
    if pattern is None:
        return None
    import re

    match = re.search(pattern.encode("utf-8"), output)
    if match is None:
        return 0
    try:
        value = int(match.group(1))
    except (IndexError, ValueError):
        return 0
    return value if value >= 0 else 0


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
    before = candidate.digest
    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    timed_out = False
    with materialize_candidate(candidate) as root:
        repro_digest = None
        if repro_input is not None:
            path, content = repro_input
            target = _target(root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            repro_digest = "sha256:" + hashlib.sha256(path.encode() + b"\0" + content).hexdigest()
        cwd = root if relative_cwd == "." else root / _relative(relative_cwd)
        if not cwd.is_dir():
            raise VerificationRunnerError("verifier-cwd-missing")
        child = subprocess.Popen(
            argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            env={"PATH": command.get("toolchain_path", os.defpath), **command.get("env", {})},
        )
        output = bytearray()
        selector = selectors.DefaultSelector()
        assert child.stdout is not None
        selector.register(child.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                os.killpg(child.pid, signal.SIGKILL)
                remaining = 0.1
            for key, _event in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(output) < limit:
                    output.extend(chunk[:limit - len(output)])
        child.wait()
        exit_code = None if timed_out else child.returncode
        selector.close()
        observed = tuple(_read(root, item.path, item.mode, required=True) for item in candidate.files)
        if _digest(observed) != candidate.digest:
            timed_out = True
            exit_code = None
    output = bytes(output)
    after = candidate.digest
    if before != after:
        raise VerificationRunnerError("candidate-mutated")
    count = _executed_count(command, output)
    passed = not timed_out and exit_code == 0 and (command.get("kind") != "test" or (count is not None and count > 0))
    return {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "executed_count": count,
        "output_digest": "sha256:" + hashlib.sha256(output).hexdigest(),
        "status": "passed" if passed else "failed" if not timed_out else "blocked",
        "repro_input_digest": repro_digest,
    }
