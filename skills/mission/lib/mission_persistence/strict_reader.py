"""Strict file reader for stable mission document snapshots."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mission_kernel.errors import StrictReadError

STATE_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class StablePathRead:
    payload: bytes
    identity: tuple[tuple[int, ...], ...]


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _lstat(path: Path, *, final: bool) -> os.stat_result:
    try:
        return os.lstat(os.fspath(path))
    except OSError as exc:
        code = "identity-changed" if final else "not-regular-single-link"
        detail = "path identity changed during read" if final else "file is missing"
        raise StrictReadError(code, detail) from exc


def _require_regular_single_link(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise StrictReadError(
            "not-regular-single-link", "file must be a regular single-link file"
        )


def read_stable_bytes(path: Path | str, *, limit: int = STATE_LIMIT) -> bytes:
    candidate = Path(path)
    initial = _lstat(candidate, final=False)
    _require_regular_single_link(initial)
    if initial.st_size > limit:
        raise StrictReadError("record-too-large", f"file exceeds {limit} bytes")
    if not hasattr(os, "O_NOFOLLOW"):
        raise StrictReadError("not-regular-single-link", "platform lacks O_NOFOLLOW")

    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(candidate), flags)
    except OSError as exc:
        raise StrictReadError(
            "not-regular-single-link", "file cannot be opened safely"
        ) from exc
    try:
        opened = os.fstat(fd)
        _require_regular_single_link(opened)
        if _identity(opened) != _identity(initial):
            raise StrictReadError("identity-changed", "file identity changed before read")

        chunks: list[bytes] = []
        remaining = initial.st_size
        while remaining:
            try:
                chunk = os.read(fd, min(remaining, 64 * 1024))
            except OSError as exc:
                raise StrictReadError("identity-changed", "file changed while being read") from exc
            if not chunk:
                raise StrictReadError("identity-changed", "file changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            extra = os.read(fd, 1)
            final_descriptor = os.fstat(fd)
        except OSError as exc:
            raise StrictReadError("identity-changed", "file changed while being read") from exc
        if extra or _identity(final_descriptor) != _identity(initial):
            raise StrictReadError("identity-changed", "file changed while being read")

        final_path = _lstat(candidate, final=True)
        if _identity(final_path) != _identity(initial):
            raise StrictReadError("identity-changed", "path identity changed during read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def read_stable_bytes_beneath(
    root: Path | str,
    relative_path: str,
    *,
    limit: int = STATE_LIMIT,
) -> StablePathRead:
    """Read a file while pinning every directory in its relative path."""
    relative = PurePosixPath(relative_path)
    if (
        not relative.parts
        or relative.is_absolute()
        or relative.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise StrictReadError("not-regular-single-link", "relative path is unsafe")
    if not hasattr(os, "O_NOFOLLOW"):
        raise StrictReadError("not-regular-single-link", "platform lacks O_NOFOLLOW")

    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    )
    descriptors: list[int] = []
    relationships: list[tuple[int, str, int]] = []
    identities: list[tuple[int, ...]] = []
    try:
        try:
            root_fd = os.open(os.fspath(root), directory_flags)
        except OSError as exc:
            raise StrictReadError(
                "not-regular-single-link", "root directory cannot be opened safely"
            ) from exc
        descriptors.append(root_fd)
        identities.append(_directory_identity(os.fstat(root_fd)))
        parent_fd = root_fd
        for part in relative.parts[:-1]:
            try:
                child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            except OSError as exc:
                raise StrictReadError(
                    "not-regular-single-link",
                    "parent directory cannot be opened safely",
                ) from exc
            descriptors.append(child_fd)
            relationships.append((parent_fd, part, child_fd))
            identities.append(_directory_identity(os.fstat(child_fd)))
            parent_fd = child_fd

        name = relative.parts[-1]
        try:
            file_fd = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise StrictReadError(
                "not-regular-single-link", "file cannot be opened safely"
            ) from exc
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        _require_regular_single_link(before)
        if before.st_size > limit:
            raise StrictReadError("record-too-large", f"file exceeds {limit} bytes")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            try:
                chunk = os.read(file_fd, min(remaining, 64 * 1024))
            except OSError as exc:
                raise StrictReadError(
                    "identity-changed", "file changed while being read"
                ) from exc
            if not chunk:
                raise StrictReadError(
                    "identity-changed", "file changed while being read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            extra = os.read(file_fd, 1)
            after = os.fstat(file_fd)
            named_file = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise StrictReadError(
                "identity-changed", "file identity changed during read"
            ) from exc
        if (
            extra
            or _identity(after) != _identity(before)
            or _identity(named_file) != _identity(before)
        ):
            raise StrictReadError(
                "identity-changed", "file identity changed during read"
            )
        for relationship_parent, part, child_fd in relationships:
            try:
                named_directory = os.stat(
                    part,
                    dir_fd=relationship_parent,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise StrictReadError(
                    "identity-changed", "parent directory identity changed"
                ) from exc
            if _directory_identity(named_directory) != _directory_identity(
                os.fstat(child_fd)
            ):
                raise StrictReadError(
                    "identity-changed", "parent directory identity changed"
                )
        identities.append(_identity(before))
        return StablePathRead(b"".join(chunks), tuple(identities))
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
