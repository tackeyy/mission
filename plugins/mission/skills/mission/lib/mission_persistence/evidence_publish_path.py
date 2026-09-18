"""Descriptor-based parent resolution for v4 evidence projections (#788).

The legacy publisher must choose an external publication directory from the
project root at the time it writes.  Path resolution follows symlinks, so it
cannot make that choice safely after the CLI's earlier path validation.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


class EvidencePublishPathError(ValueError):
    """The publication parent cannot be opened as a safe projection."""


@dataclass
class EvidencePublishDirectory:
    """A pinned parent directory and the output name to create beneath it."""

    directory_fd: int
    filename: str


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _publication_parts(path_text: str) -> tuple[str, ...]:
    if not isinstance(path_text, str) or not path_text or "\x00" in path_text:
        raise EvidencePublishPathError("evidence publication path is invalid")
    path = PurePosixPath(path_text)
    if path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise EvidencePublishPathError("evidence publication path is invalid")
    return path.parts


@contextlib.contextmanager
def open_evidence_publish_directory(
    project_root: Path, publication_path: str
) -> Iterator[EvidencePublishDirectory]:
    """Open an external evidence parent without following any path symlink.

    Every parent component is opened relative to the descriptor that named its
    predecessor.  The repository identity is checked after each open, so a
    distinct spelling of ``.mission-state`` is refused too.  The final output
    name is deliberately left unopened for the publish transaction.
    """
    parts = _publication_parts(publication_path)
    descriptors: list[int] = []
    try:
        root_fd = os.open(os.fspath(project_root), _directory_flags())
        descriptors.append(root_fd)
        try:
            repository_fd = os.open(".mission-state", _directory_flags(), dir_fd=root_fd)
        except OSError as exc:
            raise EvidencePublishPathError("evidence repository cannot be opened") from exc
        try:
            repository_identity = _identity(os.fstat(repository_fd))
        finally:
            os.close(repository_fd)

        for part in parts[:-1]:
            try:
                child_fd = os.open(part, _directory_flags(), dir_fd=descriptors[-1])
            except FileNotFoundError:
                try:
                    # Repository-external publications follow the default umask, unlike private state directories.
                    os.mkdir(part, dir_fd=descriptors[-1])
                    child_fd = os.open(part, _directory_flags(), dir_fd=descriptors[-1])
                except OSError as exc:
                    raise EvidencePublishPathError(
                        "evidence publication parent cannot be created safely"
                    ) from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise EvidencePublishPathError(
                        "evidence publication parent must not be a symlink"
                    ) from exc
                raise EvidencePublishPathError(
                    "evidence publication parent cannot be opened safely"
                ) from exc
            descriptors.append(child_fd)
            metadata = os.fstat(child_fd)
            if not stat.S_ISDIR(metadata.st_mode) or _identity(metadata) == repository_identity:
                raise EvidencePublishPathError(
                    "evidence publication parent is the repository or not a directory"
                )

        yield EvidencePublishDirectory(descriptors[-1], parts[-1])
    except OSError as exc:
        raise EvidencePublishPathError("evidence project root cannot be opened") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
