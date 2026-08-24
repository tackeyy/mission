"""Administrative separate-aggregate commit protocol (ADR-006 point 3 / U5-1).

ADR-006 rejects the category "administrative writer without a protocol": every
separate-aggregate write must provide, at minimum, an identity-checked read,
validation, an atomic publish, and a defined failure outcome.  This module
owns that minimal protocol for janitor-style record mutations
(resolve-archive).  Legacy V4/V5 saves use the separate durable-intent
protocol in ``aggregate_index`` for their rebuildable aggregate index.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping


class AdministrativeCommitError(Exception):
    """Protocol-level failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _record_identity(metadata) -> tuple:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@dataclass(frozen=True)
class CapturedRecord:
    """One identity-checked read of an administrative record."""

    path: Path
    payload: bytes
    identity: tuple
    document: dict


def capture_record(target: Path) -> CapturedRecord:
    """Identity-checked read: reject symlinks, hardlinks, and non-objects."""
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise AdministrativeCommitError("record-unreadable", str(exc)) from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AdministrativeCommitError(
            "record-identity-invalid",
            "administrative record must be a regular non-hardlinked file",
        )
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise AdministrativeCommitError("record-unreadable", str(exc)) from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdministrativeCommitError("record-invalid", str(exc)) from exc
    if not isinstance(document, dict):
        raise AdministrativeCommitError(
            "record-invalid", "administrative record must be a JSON object"
        )
    return CapturedRecord(target, payload, _record_identity(metadata), document)


def administrative_commit(
    target: Path,
    *,
    validate: Callable[[dict], None],
    mutate: Callable[[dict], None],
    write_document: Callable[..., None],
) -> tuple[CapturedRecord, dict]:
    """One identity-checked read → validation → atomic publish transaction.

    Defined failure outcomes:

    - ``validate`` / ``mutate`` errors propagate unchanged before any write;
    - an identity change between capture and publish fails closed with
      ``record-changed`` (the caller's lock makes this a crash-only path, and
      the protocol still refuses to overwrite a record it did not read);
    - a failed publish propagates from the injected atomic writer and leaves
      the original record in place (full former-or-latter state).
    """
    captured = capture_record(target)
    validate(copy.deepcopy(captured.document))
    proposed = copy.deepcopy(captured.document)
    mutate(proposed)
    try:
        current = target.lstat()
    except OSError as exc:
        raise AdministrativeCommitError("record-changed", str(exc)) from exc
    if _record_identity(current) != captured.identity:
        raise AdministrativeCommitError(
            "record-changed",
            "administrative record changed between capture and publish",
        )
    write_document(target, proposed, administrative=True)
    return captured, proposed


def restore_record(
    captured: CapturedRecord, write_bytes: Callable[[Path, bytes], None]
) -> None:
    """Defined recovery step: restore the captured payload or fail closed."""
    try:
        write_bytes(captured.path, captured.payload)
    except OSError as exc:
        raise AdministrativeCommitError("rollback-failed", str(exc)) from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        os.fspath(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory_sync(path: Path) -> None:
    try:
        _fsync_directory(path)
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-publish-failed", "archive directory cannot be synchronized"
        ) from exc


def _require_plain_directory(path: Path, *, create: bool = False) -> os.stat_result:
    created = False
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if not create:
            raise AdministrativeCommitError(
                "generation-directory-invalid", "archive directory is missing"
            )
        try:
            path.mkdir(mode=0o700)
            metadata = path.lstat()
            created = True
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-directory-invalid", "archive directory cannot be created"
            ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AdministrativeCommitError(
            "generation-directory-invalid",
            "archive destination must be a regular directory",
        )
    if created:
        _durable_directory_sync(path.parent)
    return metadata


def _safe_generation_file(value: str) -> PurePosixPath:
    candidate = PurePosixPath(value)
    if (
        not value
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise AdministrativeCommitError(
            "generation-path-invalid", "archive file path is unsafe"
        )
    return candidate


def _write_generation_file(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(
            os.fspath(path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-publish-failed", "archive file cannot be staged"
        ) from exc


def _verify_generation_files(root: Path, files: Mapping[str, bytes]) -> None:
    expected = set()
    for relative, payload in files.items():
        candidate = _safe_generation_file(relative)
        path = root.joinpath(*candidate.parts)
        expected.add(path)
        try:
            metadata = path.lstat()
            actual = path.read_bytes()
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-changed", "archive generation cannot be read"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or actual != payload
        ):
            raise AdministrativeCommitError(
                "generation-changed", "archive generation differs from staged bytes"
            )
    try:
        actual_files = {path for path in root.rglob("*") if path.is_file()}
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-changed", "archive generation cannot be enumerated"
        ) from exc
    if actual_files != expected:
        raise AdministrativeCommitError(
            "generation-changed", "archive generation contains unexpected files"
        )


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def _open_directory_at(
    parent_fd: int, name: str, *, create: bool = False
) -> tuple[int, bool]:
    created = False
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise AdministrativeCommitError(
                "generation-directory-invalid", "archive directory is missing"
            )
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            created = True
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-publish-failed",
                "archive directory cannot be created durably",
            ) from exc
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-directory-invalid", "archive destination is unsafe"
        ) from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise AdministrativeCommitError(
            "generation-directory-invalid",
            "archive destination must be a regular directory",
        )
    return descriptor, created


def _read_file_at(parent_fd: int, name: str) -> tuple[bytes, tuple]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-changed", "archive generation cannot be read safely"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AdministrativeCommitError(
                "generation-changed", "archive generation file identity is invalid"
            )
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise AdministrativeCommitError(
                    "generation-changed", "archive generation changed during read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        extra = os.read(descriptor, 1)
        after = os.fstat(descriptor)
        if extra or _record_identity(after) != _record_identity(before):
            raise AdministrativeCommitError(
                "generation-changed", "archive generation changed during read"
            )
        return b"".join(chunks), _record_identity(before)
    finally:
        os.close(descriptor)


def _verify_generation_fd(root_fd: int, files: Mapping[str, bytes]) -> None:
    actual: set[str] = set()

    def walk(directory_fd: int, prefix: tuple[str, ...]) -> None:
        try:
            names = os.listdir(directory_fd)
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-changed", "archive generation cannot be enumerated"
            ) from exc
        for name in names:
            try:
                metadata = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise AdministrativeCommitError(
                    "generation-changed", "archive generation entry changed"
                ) from exc
            parts = prefix + (name,)
            if stat.S_ISDIR(metadata.st_mode):
                child, _ = _open_directory_at(directory_fd, name)
                try:
                    walk(child, parts)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                relative = PurePosixPath(*parts).as_posix()
                payload, _identity = _read_file_at(directory_fd, name)
                if files.get(relative) != payload:
                    raise AdministrativeCommitError(
                        "generation-changed",
                        "archive generation differs from staged bytes",
                    )
                actual.add(relative)
            else:
                raise AdministrativeCommitError(
                    "generation-changed", "archive generation entry is unsafe"
                )

    walk(root_fd, ())
    if actual != set(files):
        raise AdministrativeCommitError(
            "generation-changed", "archive generation contains unexpected files"
        )


def _remove_tree_at(parent_fd: int, name: str) -> None:
    try:
        root_fd, _ = _open_directory_at(parent_fd, name)
    except AdministrativeCommitError:
        return
    try:
        for child_name in os.listdir(root_fd):
            metadata = os.stat(
                child_name, dir_fd=root_fd, follow_symlinks=False
            )
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree_at(root_fd, child_name)
            else:
                os.unlink(child_name, dir_fd=root_fd)
    except OSError:
        return
    finally:
        os.close(root_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError:
        return


def _require_named_directory_identity(
    parent_fd: int, name: str, descriptor: int
) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-changed", "archive directory identity changed"
        ) from exc
    if _record_identity(named) != _record_identity(opened):
        raise AdministrativeCommitError(
            "generation-changed", "archive directory identity changed"
        )


def _sync_published_generation_parent(generations_fd: int) -> None:
    """Make a visible generation name durable before reporting success."""
    try:
        os.fsync(generations_fd)
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-publish-failed",
            "archive generation name cannot be synchronized",
        ) from exc


def publish_administrative_generation(
    bundle: Path,
    *,
    generation: str,
    files: Mapping[str, bytes],
) -> Path:
    """Publish one content-addressed immutable administrative generation.

    This is the append-only counterpart of :func:`administrative_commit`:
    validate all immutable inputs, stage private fsynced bytes, publish the
    complete generation with one directory rename, and verify collisions
    byte-for-byte.  A failed publish leaves either no named generation or the
    complete requested generation; it never mutates an existing generation.
    """

    if re.fullmatch(r"[0-9a-f]{64}", generation) is None:
        raise AdministrativeCommitError(
            "generation-id-invalid", "archive generation id must be a sha256 hex digest"
        )
    if not files or any(type(payload) is not bytes for payload in files.values()):
        raise AdministrativeCommitError(
            "generation-input-invalid", "archive generation files must be immutable bytes"
        )
    for relative in files:
        _safe_generation_file(relative)

    authority = bundle.parent.parent
    archive_name = bundle.parent.name
    bundle_name = bundle.name
    if any(
        not name or name in {".", ".."} or "/" in name or "\\" in name
        for name in (archive_name, bundle_name)
    ):
        raise AdministrativeCommitError(
            "generation-path-invalid", "archive bundle path is unsafe"
        )
    try:
        authority_fd = os.open(os.fspath(authority), _DIRECTORY_FLAGS)
    except OSError as exc:
        raise AdministrativeCommitError(
            "generation-directory-invalid", "archive authority is unsafe"
        ) from exc
    archive_fd = bundle_fd = generations_fd = None
    stage_name = ".tmp-" + secrets.token_hex(16)
    stage_created = False
    directory_fds: dict[tuple[str, ...], int] = {}
    try:
        archive_fd, _ = _open_directory_at(
            authority_fd, archive_name, create=True
        )
        bundle_fd, _ = _open_directory_at(archive_fd, bundle_name, create=True)
        generations_fd, _ = _open_directory_at(
            bundle_fd, "generations", create=True
        )
        devices = {
            os.fstat(authority_fd).st_dev,
            os.fstat(archive_fd).st_dev,
            os.fstat(bundle_fd).st_dev,
            os.fstat(generations_fd).st_dev,
        }
        if len(devices) != 1:
            raise AdministrativeCommitError(
                "generation-filesystem-invalid",
                "archive generation must stay on one filesystem",
            )

        try:
            existing_fd = os.open(
                generation, _DIRECTORY_FLAGS, dir_fd=generations_fd
            )
        except FileNotFoundError:
            existing_fd = None
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-collision",
                "archive generation path is not a plain directory",
            ) from exc
        if existing_fd is not None:
            try:
                _verify_generation_fd(existing_fd, files)
                named = os.stat(
                    generation,
                    dir_fd=generations_fd,
                    follow_symlinks=False,
                )
                if _record_identity(named) != _record_identity(
                    os.fstat(existing_fd)
                ):
                    raise AdministrativeCommitError(
                        "generation-changed",
                        "archive generation identity changed during verification",
                    )
            finally:
                os.close(existing_fd)
            _require_named_directory_identity(
                authority_fd, archive_name, archive_fd
            )
            _require_named_directory_identity(archive_fd, bundle_name, bundle_fd)
            _require_named_directory_identity(
                bundle_fd, "generations", generations_fd
            )
            _sync_published_generation_parent(generations_fd)
            return bundle / "generations" / generation

        try:
            os.mkdir(stage_name, 0o700, dir_fd=generations_fd)
            stage_created = True
            os.fsync(generations_fd)
            stage_fd = os.open(
                stage_name, _DIRECTORY_FLAGS, dir_fd=generations_fd
            )
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-publish-failed",
                "archive generation cannot be staged durably",
            ) from exc
        directory_fds[()] = stage_fd
        for relative, payload in sorted(files.items()):
            candidate = _safe_generation_file(relative)
            prefix: tuple[str, ...] = ()
            for part in candidate.parts[:-1]:
                parent_fd = directory_fds[prefix]
                prefix = prefix + (part,)
                if prefix not in directory_fds:
                    child_fd, _ = _open_directory_at(
                        parent_fd, part, create=True
                    )
                    directory_fds[prefix] = child_fd
            parent_fd = directory_fds[prefix]
            try:
                descriptor = os.open(
                    candidate.parts[-1],
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise AdministrativeCommitError(
                    "generation-publish-failed",
                    "archive generation file cannot be staged",
                ) from exc
        try:
            for prefix in sorted(directory_fds, key=len, reverse=True):
                os.fsync(directory_fds[prefix])
        except OSError as exc:
            raise AdministrativeCommitError(
                "generation-publish-failed",
                "archive generation directory cannot be synchronized",
            ) from exc
        try:
            os.rename(
                stage_name,
                generation,
                src_dir_fd=generations_fd,
                dst_dir_fd=generations_fd,
            )
            stage_created = False
        except OSError as exc:
            try:
                collision_fd = os.open(
                    generation, _DIRECTORY_FLAGS, dir_fd=generations_fd
                )
            except OSError:
                raise AdministrativeCommitError(
                    "generation-publish-failed",
                    "archive generation cannot be published",
                ) from exc
            try:
                _verify_generation_fd(collision_fd, files)
            finally:
                os.close(collision_fd)
            _require_named_directory_identity(
                authority_fd, archive_name, archive_fd
            )
            _require_named_directory_identity(archive_fd, bundle_name, bundle_fd)
            _require_named_directory_identity(
                bundle_fd, "generations", generations_fd
            )
            _sync_published_generation_parent(generations_fd)
            return bundle / "generations" / generation
        _sync_published_generation_parent(generations_fd)
        _verify_generation_fd(stage_fd, files)
        named = os.stat(
            generation, dir_fd=generations_fd, follow_symlinks=False
        )
        if _record_identity(named) != _record_identity(os.fstat(stage_fd)):
            raise AdministrativeCommitError(
                "generation-changed",
                "archive generation identity changed after publish",
            )
        _require_named_directory_identity(authority_fd, archive_name, archive_fd)
        _require_named_directory_identity(archive_fd, bundle_name, bundle_fd)
        _require_named_directory_identity(
            bundle_fd, "generations", generations_fd
        )
        return bundle / "generations" / generation
    finally:
        for descriptor in sorted(
            set(directory_fds.values()), reverse=True
        ):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if stage_created and generations_fd is not None:
            _remove_tree_at(generations_fd, stage_name)
        for descriptor in (generations_fd, bundle_fd, archive_fd, authority_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
