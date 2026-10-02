"""Bounded actual-worktree snapshots for registered verification commands."""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
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
    if not path or path.startswith("/") or "\\" in path or len(path) >= 2 and path[1] == ":" or any(part in {"", ".", ".."} for part in path.split("/")):
        raise VerificationRunnerError("candidate-path-invalid")
    return path


def _tracked(root: Path) -> list[tuple[str, int]]:
    result = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise VerificationRunnerError("candidate-git-unavailable")
    files = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        left, raw_path = entry.split(b"\t", 1)
        mode = int(left.split(b" ", 1)[0], 8)
        files.append((_relative(raw_path.decode("utf-8")), mode))
    return files


def _read(root: Path, path: str, mode: int, *, required: bool) -> CandidateFile:
    target = root / path
    try:
        info = target.lstat()
    except FileNotFoundError:
        if required:
            return CandidateFile(path, mode, None)
        raise VerificationRunnerError("declared-output-missing") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VerificationRunnerError("candidate-special-file")
    return CandidateFile(path, stat.S_IMODE(info.st_mode), target.read_bytes())


def _digest(files) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.path.encode("utf-8") + b"\0" + str(item.mode).encode() + b"\0")
        digest.update(b"-" if item.content is None else item.content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def capture_candidate(root, *, declared_untracked) -> CandidateSnapshot:
    root = Path(root).resolve()
    tracked = _tracked(root)
    names = {path for path, _mode in tracked}
    files = [_read(root, path, mode, required=True) for path, mode in tracked]
    for path in declared_untracked:
        path = _relative(path)
        if path in names:
            raise VerificationRunnerError("declared-output-tracked")
        files.append(_read(root, path, 0, required=False))
    files.sort(key=lambda item: item.path)
    return CandidateSnapshot(tuple(files), _digest(files))


@contextlib.contextmanager
def materialize_candidate(candidate):
    if not isinstance(candidate, CandidateSnapshot) or candidate.digest != _digest(candidate.files):
        raise VerificationRunnerError("candidate-digest-invalid")
    with tempfile.TemporaryDirectory(prefix="mission-verification-") as raw:
        root = Path(raw)
        for item in candidate.files:
            if item.content is None:
                continue
            target = root / item.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.content)
            os.chmod(target, item.mode)
        yield root
