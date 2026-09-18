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
    # The written form is checked before it is parsed: ``PurePosixPath`` drops
    # a ``.`` component, so a path checked after parsing would accept
    # ``docs/./out.md`` while this walk is meant to reject every component
    # that is not a plain name.
    written = tuple(path_text.split("/"))
    if any(part in {"", ".", ".."} for part in written[:-1] + written[-1:]):
        raise EvidencePublishPathError("evidence publication path is invalid")
    path = PurePosixPath(path_text)
    if path.is_absolute() or not path.name:
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
        # Whatever way this leaves -- a refusal while opening, a refusal from
        # the body, or success -- the descriptors are released below.  The
        # inner block only turns an OSError into this module's refusal.
        try:
            root_fd = os.open(
                os.fspath(project_root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            descriptors.append(root_fd)
            try:
                repository_fd = os.open(
                    ".mission-state", _directory_flags(), dir_fd=root_fd
                )
            except OSError as exc:
                raise EvidencePublishPathError(
                    "evidence repository cannot be opened"
                ) from exc
            descriptors.append(repository_fd)

            for part in parts[:-1]:
                if part.casefold() == ".mission-state":
                    raise EvidencePublishPathError(
                        "evidence publication parent names the repository"
                    )
                try:
                    child_fd = os.open(part, _directory_flags(), dir_fd=descriptors[-2])
                except FileNotFoundError:
                    try:
                        # A repository-external publication follows the default
                        # umask, unlike the private state directories.
                        os.mkdir(part, dir_fd=descriptors[-2])
                        child_fd = os.open(
                            part, _directory_flags(), dir_fd=descriptors[-2]
                        )
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
                descriptors.insert(-1, child_fd)
                metadata = os.fstat(child_fd)
                # Taken again at every step: a repository replaced during the
                # walk would otherwise be compared against the one that is
                # gone.
                repository_identity = _identity(os.fstat(descriptors[-1]))
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or _identity(metadata) == repository_identity
                ):
                    raise EvidencePublishPathError(
                        "evidence publication parent is the repository or not a directory"
                    )
        except OSError as exc:
            raise EvidencePublishPathError(
                "evidence project root cannot be opened"
            ) from exc
        yield EvidencePublishDirectory(descriptors[-2], parts[-1])
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@dataclass
class PublicationTarget:
    """Where one effect is published, and the descriptor that pins it.

    ``directory_fd`` is ``None`` for the repository's own destinations, which
    keep the route they have always taken: the unit of work owns those, and
    the name is the repository's own.  An external projection carries the
    descriptor opened here, so the write cannot be redirected by a name that
    changes after the check (#788).
    """

    path: "Path"
    directory_fd: "int | None"


@dataclass
class PublicationParent:
    """The opened parent a publish transaction writes into."""

    directory_path: "Path"
    directory_fd: int
    directory_identity: tuple
    named_path: "Path | None"


def publication_is_in_root(publication_path: str) -> bool:
    """Say whether this publication belongs to a repository-owned rule."""
    from mission_application.evidence_publication import (
        REPOSITORY_ROOT_NAME,
        canonical_generated_path,
    )
    from mission_kernel.projection_path import (
        ProjectionRejection,
        resolve_internal_archive_path,
        resolve_internal_artifact_path,
    )

    candidate = PurePosixPath(canonical_generated_path(publication_path))
    for resolve in (resolve_internal_archive_path, resolve_internal_artifact_path):
        if not isinstance(
            resolve(candidate, root_name=REPOSITORY_ROOT_NAME), ProjectionRejection
        ):
            return True
    return False


@contextlib.contextmanager
def open_publication_directory(
    project_root: "Path", publication_path: str, resolve_named
) -> "Iterator[PublicationTarget]":
    """Choose the route for one effect and yield where it is published.

    The choice lives here rather than in the adapter: the adapter would need a
    branch to make it, and the rule that decides is the same one that owns the
    in-root destinations.
    """
    from mission_application.evidence_publication import canonical_generated_path

    if publication_is_in_root(publication_path):
        yield PublicationTarget(resolve_named(project_root, publication_path), None)
        return
    canonical = canonical_generated_path(publication_path)
    with open_evidence_publish_directory(project_root, canonical) as destination:
        parent = Path(project_root) / PurePosixPath(canonical).parent
        yield PublicationTarget(parent / destination.filename, destination.directory_fd)


def open_publication_parent(
    path: "Path", directory_fd: "int | None", open_named
) -> PublicationParent:
    """Return the parent to write into, however the caller arrived at it.

    A pinned descriptor is duplicated so that the publish transaction owns its
    copy and closing one does not close the other.  Without a descriptor the
    parent is opened by name, the way every publisher did before #788.
    """
    if directory_fd is None:
        directory_path = path.parent.resolve()
        opened_fd, identity = open_named(directory_path)
        return PublicationParent(directory_path, opened_fd, identity, directory_path)
    duplicated = os.dup(directory_fd)
    return PublicationParent(
        path.parent, duplicated, _identity3(os.fstat(duplicated)), None
    )


def _identity3(metadata: os.stat_result) -> tuple:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def publication_parent_mismatch(parent: PublicationParent):
    """Return what changed under the publish, or ``None`` when nothing did.

    A parent opened by name is compared both ways -- the descriptor and the
    name -- because either can be swapped after it was chosen.  A pinned
    parent has no name to re-read: the descriptor is the choice, so there is
    nothing here that could differ from it.
    """
    if parent.named_path is None:
        return None
    opened = os.fstat(parent.directory_fd)
    named = parent.named_path.lstat()
    if _identity3(opened) != parent.directory_identity:
        return "directory-opened", opened, named
    if _identity3(named) != parent.directory_identity:
        return "directory-named", opened, named
    return None
