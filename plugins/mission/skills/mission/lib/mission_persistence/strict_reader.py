"""Strict file reader for stable mission document snapshots."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from mission_kernel.errors import StrictReadError

STATE_LIMIT = 4 * 1024 * 1024


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
