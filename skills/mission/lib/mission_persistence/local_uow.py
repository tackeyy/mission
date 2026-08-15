"""Private staging and immutable object publication for the local UnitOfWork."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mission_kernel import decode_mission_state
from mission_kernel.errors import StrictReadError
from mission_kernel.json_codec import decode_json_object, encode_json_object, thaw_json_object

from .strict_reader import STATE_LIMIT, read_stable_bytes


MAX_BLOB_COUNT = 64
MAX_TOTAL_BLOB_BYTES = 16 * 1024 * 1024


class LocalUnitOfWorkError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _validate_relative_path(value: str) -> None:
    if not isinstance(value, str):
        raise LocalUnitOfWorkError("unsafe-relative-path", "blob reference must be text")
    candidate = PurePosixPath(value)
    if (
        not value
        or len(value) > 4096
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise LocalUnitOfWorkError("unsafe-relative-path", "blob reference must be a safe relative path")


def _validate_binding(binding: BlobBinding) -> None:
    if not isinstance(binding, BlobBinding):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "blob binding type is invalid")
    if not isinstance(binding.blob_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", binding.blob_id
    ):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "blob id is invalid")
    if not isinstance(binding.kind, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", binding.kind
    ):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "blob kind is invalid")
    try:
        _validate_relative_path(binding.relative_path)
    except LocalUnitOfWorkError as exc:
        raise LocalUnitOfWorkError("blob-binding-mismatch", exc.detail) from exc
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", binding.digest):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "blob digest is invalid")
    if type(binding.size) is not int or not 0 <= binding.size <= STATE_LIMIT:
        raise LocalUnitOfWorkError("blob-binding-mismatch", "blob size is invalid")


def _validate_blob_bindings(
    effects: tuple[BlobBinding, ...], blobs: VerifiedBlobSet
) -> None:
    if (
        type(effects) is not tuple
        or not isinstance(blobs, VerifiedBlobSet)
        or type(blobs.blobs) is not tuple
    ):
        raise LocalUnitOfWorkError(
            "immutable-input-required", "effects and verified blobs must be immutable"
        )
    if len(blobs.blobs) > MAX_BLOB_COUNT:
        raise LocalUnitOfWorkError(
            "blob-set-too-large", "verified blob count exceeds the aggregate limit"
        )
    effect_by_id = {}
    for effect in effects:
        _validate_binding(effect)
        if effect.blob_id in effect_by_id:
            raise LocalUnitOfWorkError("blob-binding-mismatch", "effect blob id is duplicated")
        effect_by_id[effect.blob_id] = effect
    blob_by_id = {}
    total_blob_bytes = 0
    for blob in blobs.blobs:
        _validate_binding(blob.binding)
        total_blob_bytes += blob.binding.size
        if total_blob_bytes > MAX_TOTAL_BLOB_BYTES:
            raise LocalUnitOfWorkError(
                "blob-set-too-large", "verified blob bytes exceed the aggregate limit"
            )
        if blob.binding.blob_id in blob_by_id:
            raise LocalUnitOfWorkError("blob-binding-mismatch", "captured blob id is duplicated")
        if type(blob.content) is not bytes:
            raise LocalUnitOfWorkError("blob-binding-mismatch", "captured blob is not immutable bytes")
        digest = "sha256:" + hashlib.sha256(blob.content).hexdigest()
        if digest != blob.binding.digest or len(blob.content) != blob.binding.size:
            raise LocalUnitOfWorkError("blob-binding-mismatch", "captured blob content changed")
        blob_by_id[blob.binding.blob_id] = blob
    if set(effect_by_id) != set(blob_by_id):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "effect and blob ids do not match")
    if any(effect_by_id[blob_id] != blob_by_id[blob_id].binding for blob_id in blob_by_id):
        raise LocalUnitOfWorkError("blob-binding-mismatch", "effect and captured blob bindings differ")


@dataclass(frozen=True)
class BlobBinding:
    blob_id: str
    kind: str
    relative_path: str
    digest: str
    size: int


@dataclass(frozen=True)
class BlobSource:
    blob_id: str
    kind: str
    relative_path: str
    source_path: Path
    limit: int = STATE_LIMIT


@dataclass(frozen=True)
class VerifiedBlob:
    binding: BlobBinding
    content: bytes


@dataclass(frozen=True)
class VerifiedBlobSet:
    blobs: tuple[VerifiedBlob, ...]


@dataclass(frozen=True)
class StagedGeneration:
    root: Path
    state_path: Path
    blob_paths: tuple[Path, ...]
    manifest_path: Path
    manifest_bytes: bytes
    generation_digest: str
    root_identity: tuple[int, int, int]
    objects_identity: tuple[int, int, int]
    state_identity: tuple[int, int, int, int, int, int, int]
    blob_identities: tuple[tuple[int, int, int, int, int, int, int], ...]
    manifest_identity: tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True)
class PublishedGeneration:
    manifest_path: Path
    object_paths: tuple[Path, ...]
    generation_digest: str
    reused: bool


@dataclass(frozen=True)
class _ValidatedStage:
    state_bytes: bytes
    blob_bytes: tuple[bytes, ...]
    transactions_identity: tuple[int, int, int]
    root_identity: tuple[int, int, int]
    objects_identity: tuple[int, int, int]


@dataclass(frozen=True)
class _PublishedLink:
    path: Path
    source_descriptor: int
    destination_descriptor: int
    destination_directory_identity: tuple[int, int, int]


class _PublishAttempt:
    """Track whether one no-overwrite link syscall created its target."""

    def __init__(self) -> None:
        self.attempted = False
        self.conflict = False
        self.completed = False

    def owns_named_target(
        self,
        source_descriptor: int,
        destination_descriptor: int,
        name: str,
    ) -> bool:
        if not self.attempted or self.conflict:
            return False
        try:
            source = os.fstat(source_descriptor)
            destination = os.stat(
                name,
                dir_fd=destination_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            return False
        return (
            stat.S_ISREG(source.st_mode)
            and stat.S_ISREG(destination.st_mode)
            and source.st_dev == destination.st_dev
            and source.st_ino == destination.st_ino
            and source.st_mode == destination.st_mode
            and source.st_size == destination.st_size
        )


@contextlib.contextmanager
def _defer_publish_signals():
    """Close the syscall/ownership gap where pthread signal masks exist."""
    mask = getattr(signal, "pthread_sigmask", None)
    previous_mask = None
    if mask is not None:
        try:
            previous_mask = mask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        except (OSError, ValueError):
            previous_mask = None
    try:
        yield
    finally:
        if previous_mask is not None:
            mask(signal.SIG_SETMASK, previous_mask)


class _PublishedLinksTransaction:
    def __init__(self) -> None:
        self._published: list[_PublishedLink] = []

    def __enter__(self) -> _PublishedLinksTransaction:
        return self

    def add(self, published: _PublishedLink | None) -> bool:
        if published is None:
            return False
        self._published.append(published)
        return True

    def contains(self, published: _PublishedLink) -> bool:
        return any(candidate is published for candidate in self._published)

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if exc_type is None:
            for published in self._published:
                _close_published_link(published)
            return False
        rollback_error = None
        for published in reversed(self._published):
            try:
                _rollback_published_link(published)
            except LocalUnitOfWorkError as error:
                if rollback_error is None:
                    rollback_error = error
        if rollback_error is not None:
            raise rollback_error
        return False


def capture_verified_blob_set(sources: tuple[BlobSource, ...]) -> VerifiedBlobSet:
    if type(sources) is not tuple:
        raise LocalUnitOfWorkError(
            "immutable-input-required", "blob sources must be an immutable tuple"
        )
    if len(sources) > MAX_BLOB_COUNT:
        raise LocalUnitOfWorkError(
            "blob-set-too-large", "blob source count exceeds the aggregate limit"
        )
    seen_blob_ids = set()
    for source in sources:
        if not isinstance(source, BlobSource):
            raise LocalUnitOfWorkError(
                "immutable-input-required", "blob source type is invalid"
            )
        if type(source.limit) is not int or not 0 <= source.limit <= STATE_LIMIT:
            raise LocalUnitOfWorkError(
                "blob-source-limit-invalid", "blob source limit must be bounded"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", source.blob_id):
            raise LocalUnitOfWorkError("blob-binding-mismatch", "blob id is invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", source.kind):
            raise LocalUnitOfWorkError("blob-binding-mismatch", "blob kind is invalid")
        _validate_relative_path(source.relative_path)
        if source.blob_id in seen_blob_ids:
            raise LocalUnitOfWorkError(
                "blob-binding-mismatch", "captured blob id is duplicated"
            )
        seen_blob_ids.add(source.blob_id)
    blobs = []
    total_blob_bytes = 0
    for source in sources:
        remaining_budget = MAX_TOTAL_BLOB_BYTES - total_blob_bytes
        effective_limit = min(source.limit, remaining_budget)
        try:
            content = read_stable_bytes(source.source_path, limit=effective_limit)
        except StrictReadError as exc:
            if exc.code == "record-too-large" and effective_limit < source.limit:
                raise LocalUnitOfWorkError(
                    "blob-set-too-large", "blob source bytes exceed the aggregate limit"
                ) from exc
            raise
        total_blob_bytes += len(content)
        if total_blob_bytes > MAX_TOTAL_BLOB_BYTES:
            raise LocalUnitOfWorkError(
                "blob-set-too-large", "captured blob bytes exceed the aggregate limit"
            )
        blobs.append(
            VerifiedBlob(
                binding=BlobBinding(
                    blob_id=source.blob_id,
                    kind=source.kind,
                    relative_path=source.relative_path,
                    digest="sha256:" + hashlib.sha256(content).hexdigest(),
                    size=len(content),
                ),
                content=content,
            )
        )
    return VerifiedBlobSet(tuple(blobs))


def _fsync(descriptor: int) -> None:
    os.fsync(descriptor)


def _write_private_file(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(path), flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LocalUnitOfWorkError("stage-write-failed", "staged file write made no progress")
            view = view[written:]
        _fsync(descriptor)
    finally:
        os.close(descriptor)
    staged = path.lstat()
    if (
        not stat.S_ISREG(staged.st_mode)
        or staged.st_nlink != 1
        or stat.S_IMODE(staged.st_mode) != 0o600
        or staged.st_size != len(content)
        or read_stable_bytes(path, limit=max(len(content), 1)) != content
    ):
        raise LocalUnitOfWorkError("staged-object-changed", "staged file failed identity validation")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        metadata = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_dev != named.st_dev
            or metadata.st_ino != named.st_ino
        ):
            raise LocalUnitOfWorkError("stage-directory-changed", "staging directory changed")
        _fsync(descriptor)
    finally:
        os.close(descriptor)


def _object_name(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest() + ".blob"


def _stage_object(objects: Path, content: bytes) -> Path:
    path = objects / _object_name(content)
    if path.exists() or path.is_symlink():
        _read_expected_file(path, content, code="content-address-collision")
        return path
    _write_private_file(path, content)
    return path


def _require_directory(path: Path, *, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LocalUnitOfWorkError(code, "required directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise LocalUnitOfWorkError(code, "required path is not a regular directory")
    return metadata


def _ensure_directory(path: Path, *, mode: int, code: str) -> os.stat_result:
    try:
        path.mkdir(mode=mode, exist_ok=True)
    except OSError as exc:
        raise LocalUnitOfWorkError(code, "directory cannot be created safely") from exc
    return _require_directory(path, code=code)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _matches_staged_file_identity(
    metadata: os.stat_result,
    expected: tuple[int, int, int, int, int, int, int],
    *,
    allowed_link_counts: tuple[int, ...],
) -> bool:
    actual = _file_identity(metadata)
    if actual[3] not in allowed_link_counts:
        return False
    if actual[3] == expected[3]:
        return actual == expected
    return (
        actual[0] == expected[0]
        and actual[1] == expected[1]
        and actual[2] == expected[2]
        and actual[4] == expected[4]
        and actual[5] == expected[5]
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _open_directory(path: Path, *, code: str) -> tuple[int, tuple[int, int, int]]:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise LocalUnitOfWorkError(code, "platform cannot pin directories safely")
    try:
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise LocalUnitOfWorkError(code, "directory cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
        identity = _directory_identity(opened)
        if not stat.S_ISDIR(opened.st_mode) or _directory_identity(named) != identity:
            raise LocalUnitOfWorkError(code, "directory identity changed")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _verify_directory(
    descriptor: int,
    path: Path,
    identity: tuple[int, int, int],
    *,
    code: str,
) -> None:
    try:
        if (
            _directory_identity(os.fstat(descriptor)) != identity
            or _directory_identity(path.lstat()) != identity
        ):
            raise LocalUnitOfWorkError(code, "directory identity changed")
    except LocalUnitOfWorkError:
        raise
    except OSError as exc:
        raise LocalUnitOfWorkError(code, "directory identity changed") from exc


def _create_private_stage_root(
    transactions: Path,
    transactions_descriptor: int,
    transactions_identity: tuple[int, int, int],
) -> tuple[Path, tuple[int, int, int]]:
    """Create a private stage under the pinned transactions directory."""
    _verify_directory(
        transactions_descriptor,
        transactions,
        transactions_identity,
        code="stage-directory-changed",
    )
    name = ""
    for _ in range(32):
        candidate = ".stage-" + secrets.token_hex(16)
        try:
            os.mkdir(candidate, 0o700, dir_fd=transactions_descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage cannot be created safely"
            ) from exc
        name = candidate
        break
    if not name:
        raise LocalUnitOfWorkError(
            "stage-directory-changed", "private stage name could not be allocated"
        )

    root_descriptor = None
    try:
        root_descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=transactions_descriptor,
        )
        os.fchmod(root_descriptor, 0o700)
        root_metadata = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
        ):
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity is invalid"
            )
        root_identity = _directory_identity(root_metadata)
        _verify_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            code="stage-directory-changed",
        )
        root = transactions / name
        if _directory_identity(root.lstat()) != root_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            )
        return root, root_identity
    except BaseException:
        try:
            named = os.stat(name, dir_fd=transactions_descriptor, follow_symlinks=False)
            if stat.S_ISDIR(named.st_mode):
                os.rmdir(name, dir_fd=transactions_descriptor)
        except OSError:
            pass
        raise
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)


def _fsync_pinned_repository(
    repository: Path,
    repository_descriptor: int,
    repository_identity: tuple[int, int, int],
    transactions: Path,
    transactions_descriptor: int,
    transactions_identity: tuple[int, int, int],
    root: Path,
    root_identity: tuple[int, int, int],
) -> None:
    def verify_hierarchy() -> None:
        _verify_directory(
            repository_descriptor,
            repository,
            repository_identity,
            code="stage-directory-changed",
        )
        _verify_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            code="stage-directory-changed",
        )
        try:
            if _directory_identity(root.lstat()) != root_identity:
                raise LocalUnitOfWorkError(
                    "stage-directory-changed", "private stage identity changed"
                )
        except LocalUnitOfWorkError:
            raise
        except OSError as exc:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            ) from exc

    verify_hierarchy()
    _fsync(repository_descriptor)
    verify_hierarchy()


def _read_expected_file_at(
    directory_descriptor: int,
    directory_path: Path,
    directory_identity: tuple[int, int, int],
    name: str,
    expected: bytes,
    *,
    code: str,
    allowed_link_counts: tuple[int, ...] = (1,),
    required_mode: int | None = None,
    expected_identity: tuple[int, int, int, int, int, int, int] | None = None,
) -> None:
    try:
        _verify_directory(
            directory_descriptor,
            directory_path,
            directory_identity,
            code=code,
        )
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink not in allowed_link_counts
            or metadata.st_size != len(expected)
            or (required_mode is not None and stat.S_IMODE(metadata.st_mode) != required_mode)
        ):
            raise LocalUnitOfWorkError(code, "immutable file identity is invalid")
        if expected_identity is not None and not _matches_staged_file_identity(
            metadata,
            expected_identity,
            allowed_link_counts=allowed_link_counts,
        ):
            raise LocalUnitOfWorkError(code, "immutable file differs from its staged identity")
        if not hasattr(os, "O_NOFOLLOW"):
            raise LocalUnitOfWorkError(code, "platform cannot reject linked files")
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        try:
            opened = os.fstat(descriptor)
            if _file_identity(opened) != _file_identity(metadata):
                raise LocalUnitOfWorkError(code, "immutable file identity changed")
            chunks = []
            remaining = len(expected)
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    raise LocalUnitOfWorkError(code, "immutable file content changed")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise LocalUnitOfWorkError(code, "immutable file content changed")
            final_descriptor = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        final_path = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            _file_identity(final_descriptor) != _file_identity(metadata)
            or _file_identity(final_path) != _file_identity(metadata)
        ):
            raise LocalUnitOfWorkError(code, "immutable file identity changed")
        _verify_directory(
            directory_descriptor,
            directory_path,
            directory_identity,
            code=code,
        )
        actual = b"".join(chunks)
    except LocalUnitOfWorkError:
        raise
    except Exception as exc:
        raise LocalUnitOfWorkError(code, "immutable file cannot be read safely") from exc
    if actual != expected or hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
        raise LocalUnitOfWorkError(code, "immutable file content differs")


def _read_expected_file(
    path: Path,
    expected: bytes,
    *,
    code: str,
    allowed_link_counts: tuple[int, ...] = (1,),
    required_mode: int | None = None,
    expected_identity: tuple[int, int, int, int, int, int, int] | None = None,
) -> None:
    descriptor, identity = _open_directory(path.parent, code=code)
    try:
        _read_expected_file_at(
            descriptor,
            path.parent,
            identity,
            path.name,
            expected,
            code=code,
            allowed_link_counts=allowed_link_counts,
            required_mode=required_mode,
            expected_identity=expected_identity,
        )
    finally:
        os.close(descriptor)


def _close_published_link(published: _PublishedLink) -> None:
    os.close(published.destination_descriptor)
    os.close(published.source_descriptor)


def _rollback_published_link(published: _PublishedLink) -> None:
    """Unlink only the destination still backed by the pinned staged inode."""
    try:
        opened_destination_directory = os.fstat(published.destination_descriptor)
        if (
            not stat.S_ISDIR(opened_destination_directory.st_mode)
            or _directory_identity(opened_destination_directory)
            != published.destination_directory_identity
        ):
            raise LocalUnitOfWorkError(
                "immutable-generation-changed",
                "published link directory no longer belongs to this transaction",
            )
        source = os.fstat(published.source_descriptor)
        destination = os.stat(
            published.path.name,
            dir_fd=published.destination_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(source.st_mode)
            or not stat.S_ISREG(destination.st_mode)
            or source.st_dev != destination.st_dev
            or source.st_ino != destination.st_ino
            or source.st_mode != destination.st_mode
            or source.st_size != destination.st_size
        ):
            raise LocalUnitOfWorkError(
                "immutable-generation-changed",
                "published link no longer belongs to this transaction",
            )
        os.unlink(published.path.name, dir_fd=published.destination_descriptor)
        _fsync(published.destination_descriptor)
    except LocalUnitOfWorkError:
        raise
    except OSError as exc:
        raise LocalUnitOfWorkError(
            "immutable-generation-changed",
            "published link rollback failed",
        ) from exc
    finally:
        _close_published_link(published)


def _pin_completed_link_result(
    publish_attempt: _PublishAttempt,
    source_directory_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_directory_identity: tuple[int, int, int],
    destination_name: str,
    expected_size: int,
) -> int:
    """Pin the exact named hardlink created by a completed link syscall."""
    if not publish_attempt.completed or publish_attempt.conflict:
        raise LocalUnitOfWorkError(
            "immutable-generation-changed", "publish attempt has no completed link"
        )
    descriptor = None
    try:
        opened_directory = os.fstat(destination_descriptor)
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or _directory_identity(opened_directory) != destination_directory_identity
        ):
            raise LocalUnitOfWorkError(
                "immutable-generation-changed", "publish directory identity changed"
            )
        descriptor = os.open(
            destination_name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=destination_descriptor,
        )
        opened_destination = os.fstat(descriptor)
        named_destination = os.stat(
            destination_name,
            dir_fd=destination_descriptor,
            follow_symlinks=False,
        )
        named_source = os.stat(
            source_name,
            dir_fd=source_directory_descriptor,
            follow_symlinks=False,
        )
        if (
            _file_identity(opened_destination) != _file_identity(named_destination)
            or not stat.S_ISREG(named_source.st_mode)
            or not stat.S_ISREG(named_destination.st_mode)
            or named_source.st_dev != named_destination.st_dev
            or named_source.st_ino != named_destination.st_ino
            or named_source.st_mode != named_destination.st_mode
            or stat.S_IMODE(named_destination.st_mode) != 0o600
            or named_source.st_size != expected_size
            or named_destination.st_size != expected_size
            or named_source.st_nlink != 2
            or named_destination.st_nlink != 2
        ):
            raise LocalUnitOfWorkError(
                "immutable-generation-changed", "completed link result cannot be pinned safely"
            )
        result = descriptor
        descriptor = None
        return result
    except LocalUnitOfWorkError:
        raise
    except OSError as exc:
        raise LocalUnitOfWorkError(
            "immutable-generation-changed", "completed link result cannot be pinned safely"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unlink_pinned_invalid_link(
    publish_attempt: _PublishAttempt,
    source_directory_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
    destination_file_descriptor: int,
    expected_size: int,
) -> bool:
    """Remove only a completed link whose invalid inode is still fully pinned."""
    if not publish_attempt.completed or publish_attempt.conflict:
        return False
    try:
        opened_destination = os.fstat(destination_file_descriptor)
        named_destination = os.stat(
            destination_name,
            dir_fd=destination_descriptor,
            follow_symlinks=False,
        )
        named_source = os.stat(
            source_name,
            dir_fd=source_directory_descriptor,
            follow_symlinks=False,
        )
        if (
            _file_identity(opened_destination) != _file_identity(named_destination)
            or not stat.S_ISREG(named_source.st_mode)
            or not stat.S_ISREG(named_destination.st_mode)
            or named_source.st_dev != named_destination.st_dev
            or named_source.st_ino != named_destination.st_ino
            or named_source.st_mode != named_destination.st_mode
            or stat.S_IMODE(named_destination.st_mode) != 0o600
            or named_source.st_size != expected_size
            or named_destination.st_size != expected_size
            or named_source.st_nlink != 2
            or named_destination.st_nlink != 2
        ):
            return False
        os.unlink(destination_name, dir_fd=destination_descriptor)
        _fsync(destination_descriptor)
        return True
    except OSError:
        return False


def _read_staged_object(
    path: Path,
    *,
    digest: str,
    size: int,
    expected_identity: tuple[int, int, int, int, int, int, int],
) -> bytes:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != size
            or _file_identity(metadata) != expected_identity
        ):
            raise LocalUnitOfWorkError(
                "staged-object-changed", "staged object identity differs from its manifest"
            )
        content = read_stable_bytes(path, limit=size)
    except LocalUnitOfWorkError:
        raise
    except Exception as exc:
        raise LocalUnitOfWorkError(
            "staged-object-changed", "staged object cannot be read safely"
        ) from exc
    if len(content) != size or "sha256:" + hashlib.sha256(content).hexdigest() != digest:
        raise LocalUnitOfWorkError(
            "staged-object-changed", "staged object content differs from its manifest"
        )
    return content


def _validate_staged_generation(
    repository: Path,
    staged: StagedGeneration,
) -> _ValidatedStage:
    if not isinstance(staged, StagedGeneration):
        raise LocalUnitOfWorkError(
            "immutable-input-required", "staged generation type is invalid"
        )
    repository_metadata = _require_directory(repository, code="repository-changed")
    transactions = repository / "transactions"
    transactions_metadata = _require_directory(transactions, code="stage-directory-changed")
    if staged.root.parent != transactions:
        raise LocalUnitOfWorkError(
            "stage-directory-changed", "staged generation is outside the transaction directory"
        )
    stage_metadata = _require_directory(staged.root, code="stage-directory-changed")
    objects = staged.root / "objects"
    objects_metadata = _require_directory(objects, code="stage-directory-changed")
    if (
        stage_metadata.st_dev != repository_metadata.st_dev
        or objects_metadata.st_dev != repository_metadata.st_dev
        or stat.S_IMODE(stage_metadata.st_mode) != 0o700
        or stat.S_IMODE(objects_metadata.st_mode) != 0o700
        or _directory_identity(stage_metadata) != staged.root_identity
        or _directory_identity(objects_metadata) != staged.objects_identity
    ):
        raise LocalUnitOfWorkError(
            "stage-filesystem-mismatch", "staging must be private on the repository filesystem"
        )
    if staged.manifest_path != staged.root / "manifest.json":
        raise LocalUnitOfWorkError("staged-manifest-changed", "manifest path differs")
    expected_generation_digest = "sha256:" + hashlib.sha256(staged.manifest_bytes).hexdigest()
    if staged.generation_digest != expected_generation_digest:
        raise LocalUnitOfWorkError("staged-manifest-changed", "manifest digest differs")
    _read_expected_file(
        staged.manifest_path,
        staged.manifest_bytes,
        code="staged-manifest-changed",
        required_mode=0o600,
        expected_identity=staged.manifest_identity,
    )
    try:
        frozen_manifest = decode_json_object(staged.manifest_bytes, limit=STATE_LIMIT)
        if encode_json_object(frozen_manifest) != staged.manifest_bytes:
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "manifest is not canonical JSON"
            )
        manifest = thaw_json_object(frozen_manifest)
    except LocalUnitOfWorkError:
        raise
    except Exception as exc:
        raise LocalUnitOfWorkError(
            "staged-manifest-changed", "manifest is not strict JSON"
        ) from exc
    if set(manifest) != {"schema", "state", "blobs"} or manifest.get("schema") != "mission-generation/1":
        raise LocalUnitOfWorkError("staged-manifest-changed", "manifest envelope differs")
    state_record = manifest.get("state")
    blob_records = manifest.get("blobs")
    if not isinstance(state_record, dict) or set(state_record) != {"digest", "size", "object"}:
        raise LocalUnitOfWorkError("staged-manifest-changed", "state record differs")
    if not isinstance(blob_records, list) or len(blob_records) != len(staged.blob_paths):
        raise LocalUnitOfWorkError("staged-manifest-changed", "blob record count differs")

    def validate_object_record(
        record: dict,
        path: Path,
        expected_identity: tuple[int, int, int, int, int, int, int],
    ) -> bytes:
        digest = record.get("digest")
        size = record.get("size")
        object_reference = record.get("object")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or type(size) is not int
            or not 0 <= size <= STATE_LIMIT
            or object_reference != "objects/" + digest.removeprefix("sha256:") + ".blob"
            or path != staged.root / object_reference
        ):
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "object record does not bind its content address"
            )
        return _read_staged_object(
            path,
            digest=digest,
            size=size,
            expected_identity=expected_identity,
        )

    state_bytes = validate_object_record(
        state_record,
        staged.state_path,
        staged.state_identity,
    )
    try:
        decode_mission_state(state_bytes)
    except Exception as exc:
        raise LocalUnitOfWorkError(
            "staged-object-changed", "staged state is no longer a valid mission state"
        ) from exc
    seen_blob_ids = set()
    blob_bytes = []
    if len(staged.blob_identities) != len(staged.blob_paths):
        raise LocalUnitOfWorkError(
            "staged-object-changed", "staged object identity count differs"
        )
    for record, path, expected_identity in zip(
        blob_records,
        staged.blob_paths,
        staged.blob_identities,
    ):
        if not isinstance(record, dict) or set(record) != {
            "blob_id",
            "kind",
            "relative_path",
            "digest",
            "size",
            "object",
        }:
            raise LocalUnitOfWorkError("staged-manifest-changed", "blob record differs")
        binding = BlobBinding(
            blob_id=record.get("blob_id"),
            kind=record.get("kind"),
            relative_path=record.get("relative_path"),
            digest=record.get("digest"),
            size=record.get("size"),
        )
        try:
            _validate_binding(binding)
        except (AttributeError, TypeError, LocalUnitOfWorkError) as exc:
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "blob binding differs"
            ) from exc
        if binding.blob_id in seen_blob_ids:
            raise LocalUnitOfWorkError("staged-manifest-changed", "blob id is duplicated")
        seen_blob_ids.add(binding.blob_id)
        blob_bytes.append(validate_object_record(record, path, expected_identity))

    expected_objects = {staged.state_path, *staged.blob_paths}
    try:
        if set(objects.iterdir()) != expected_objects or set(staged.root.iterdir()) != {
            objects,
            staged.manifest_path,
        }:
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "staging contains missing or extra objects"
            )
    except OSError as exc:
        raise LocalUnitOfWorkError(
            "stage-directory-changed", "staging directory changed"
        ) from exc
    return _ValidatedStage(
        state_bytes=state_bytes,
        blob_bytes=tuple(blob_bytes),
        transactions_identity=_directory_identity(transactions_metadata),
        root_identity=staged.root_identity,
        objects_identity=staged.objects_identity,
    )


def _publish_immutable_file(
    source: Path,
    destination: Path,
    content: bytes,
    *,
    expected_identity: tuple[int, int, int, int, int, int, int],
    publication: _PublishedLinksTransaction,
) -> _PublishedLink | None:
    """Publish one verified file without overwriting an existing name."""
    source_directory_descriptor, source_directory_identity = _open_directory(
        source.parent,
        code="staged-object-changed",
    )
    destination_descriptor, destination_directory_identity = _open_directory(
        destination.parent,
        code="immutable-generation-publish-failed",
    )
    source_descriptor = None
    destination_file_descriptor = None
    keep_descriptors = False
    publish_attempt = _PublishAttempt()
    published = None
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise LocalUnitOfWorkError(
                "staged-object-changed", "platform cannot pin staged files safely"
            )
        source_descriptor = os.open(
            source.name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=source_directory_descriptor,
        )
        opened_source = os.fstat(source_descriptor)
        named_source = os.stat(
            source.name,
            dir_fd=source_directory_descriptor,
            follow_symlinks=False,
        )
        if (
            _file_identity(opened_source) != expected_identity
            or _file_identity(named_source) != expected_identity
        ):
            raise LocalUnitOfWorkError(
                "staged-object-changed", "staged object differs from its staged identity"
            )
        _read_expected_file_at(
            source_directory_descriptor,
            source.parent,
            source_directory_identity,
            source.name,
            content,
            code="staged-object-changed",
            expected_identity=expected_identity,
        )
        try:
            with _defer_publish_signals():
                publish_attempt.attempted = True
                try:
                    os.link(
                        source.name,
                        destination.name,
                        src_dir_fd=source_directory_descriptor,
                        dst_dir_fd=destination_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    publish_attempt.conflict = True
                    raise
                publish_attempt.completed = True
                destination_file_descriptor = _pin_completed_link_result(
                    publish_attempt,
                    source_directory_descriptor,
                    source.name,
                    destination_descriptor,
                    destination_directory_identity,
                    destination.name,
                    len(content),
                )
                linked_destination = os.fstat(destination_file_descriptor)
                pinned_source = os.fstat(source_descriptor)
                if (
                    pinned_source.st_dev != linked_destination.st_dev
                    or pinned_source.st_ino != linked_destination.st_ino
                ):
                    if not _unlink_pinned_invalid_link(
                        publish_attempt,
                        source_directory_descriptor,
                        source.name,
                        destination_descriptor,
                        destination.name,
                        destination_file_descriptor,
                        len(content),
                    ):
                        raise LocalUnitOfWorkError(
                            "immutable-generation-changed",
                            "invalid completed link could not be removed safely",
                        )
                    raise LocalUnitOfWorkError(
                        "immutable-generation-changed",
                        "completed link used a replaced staged object",
                    )
                published = _PublishedLink(
                    path=destination,
                    source_descriptor=source_descriptor,
                    destination_descriptor=destination_descriptor,
                    destination_directory_identity=destination_directory_identity,
                )
                publication.add(published)
                keep_descriptors = True
        except FileExistsError:
            _read_expected_file_at(
                destination_descriptor,
                destination.parent,
                destination_directory_identity,
                destination.name,
                content,
                code="immutable-generation-collision",
            )
            return None
        except OSError as exc:
            raise LocalUnitOfWorkError(
                "immutable-generation-publish-failed", "immutable file could not be published"
            ) from exc
        _fsync(destination_descriptor)
        _read_expected_file_at(
            destination_descriptor,
            destination.parent,
            destination_directory_identity,
            destination.name,
            content,
            code="immutable-generation-changed",
            allowed_link_counts=(2,),
            expected_identity=expected_identity,
        )
        _read_expected_file_at(
            source_directory_descriptor,
            source.parent,
            source_directory_identity,
            source.name,
            content,
            code="staged-object-changed",
            allowed_link_counts=(2,),
            required_mode=0o600,
            expected_identity=expected_identity,
        )
        if published is None or not publication.contains(published):
            raise LocalUnitOfWorkError(
                "immutable-generation-changed", "published link ownership was not recorded"
            )
        return published
    except BaseException:
        if published is not None and publication.contains(published):
            keep_descriptors = True
        if (
            not keep_descriptors
            and source_descriptor is not None
            and publish_attempt.owns_named_target(
                source_descriptor,
                destination_descriptor,
                destination.name,
            )
        ):
            published = _PublishedLink(
                path=destination,
                source_descriptor=source_descriptor,
                destination_descriptor=destination_descriptor,
                destination_directory_identity=destination_directory_identity,
            )
            source_descriptor = None
            keep_descriptors = True
            _rollback_published_link(published)
        raise
    finally:
        os.close(source_directory_descriptor)
        if destination_file_descriptor is not None:
            os.close(destination_file_descriptor)
        if not keep_descriptors:
            os.close(destination_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)


def _open_child_directory(
    parent_descriptor: int,
    parent_path: Path,
    parent_identity: tuple[int, int, int],
    name: str,
    expected_identity: tuple[int, int, int],
    *,
    code: str,
) -> int:
    _verify_directory(
        parent_descriptor,
        parent_path,
        parent_identity,
        code=code,
    )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise LocalUnitOfWorkError(code, "platform cannot pin directories safely")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise LocalUnitOfWorkError(code, "staging directory cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(opened) != expected_identity
            or _directory_identity(named) != expected_identity
        ):
            raise LocalUnitOfWorkError(code, "staging directory identity changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _remove_validated_stage(
    repository: Path,
    staged: StagedGeneration,
    validated: _ValidatedStage,
) -> None:
    """Remove only the verified private stage; never recurse through a path."""
    transactions = repository / "transactions"
    transactions_descriptor, transactions_identity = _open_directory(
        transactions,
        code="stage-directory-changed",
    )
    root_descriptor = None
    objects_descriptor = None
    cleanup_name = ".cleanup-" + secrets.token_hex(16)
    try:
        if transactions_identity != validated.transactions_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "transaction directory identity changed"
            )
        root_descriptor = _open_child_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            staged.root.name,
            validated.root_identity,
            code="stage-directory-changed",
        )
        objects_descriptor = _open_child_directory(
            root_descriptor,
            staged.root,
            validated.root_identity,
            "objects",
            validated.objects_identity,
            code="stage-directory-changed",
        )

        object_contents = {
            _object_name(validated.state_bytes): validated.state_bytes,
        }
        object_identities = {
            _object_name(validated.state_bytes): staged.state_identity,
        }
        for content, identity in zip(validated.blob_bytes, staged.blob_identities):
            object_contents[_object_name(content)] = content
            object_identities[_object_name(content)] = identity
        for name, content in object_contents.items():
            _read_expected_file_at(
                objects_descriptor,
                staged.root / "objects",
                validated.objects_identity,
                name,
                content,
                code="staged-object-changed",
                allowed_link_counts=(1, 2),
                required_mode=0o600,
                expected_identity=object_identities[name],
            )
        _read_expected_file_at(
            root_descriptor,
            staged.root,
            validated.root_identity,
            "manifest.json",
            staged.manifest_bytes,
            code="staged-manifest-changed",
            allowed_link_counts=(1, 2),
            required_mode=0o600,
            expected_identity=staged.manifest_identity,
        )

        os.rename(
            staged.root.name,
            cleanup_name,
            src_dir_fd=transactions_descriptor,
            dst_dir_fd=transactions_descriptor,
        )
        moved = os.stat(
            cleanup_name,
            dir_fd=transactions_descriptor,
            follow_symlinks=False,
        )
        if _directory_identity(moved) != validated.root_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "renamed staging directory identity changed"
            )

        for name in object_contents:
            os.unlink(name, dir_fd=objects_descriptor)
        _fsync(objects_descriptor)
        os.close(objects_descriptor)
        objects_descriptor = None
        named_objects = os.stat(
            "objects",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if _directory_identity(named_objects) != validated.objects_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "staged object directory identity changed"
            )
        os.rmdir("objects", dir_fd=root_descriptor)
        os.unlink("manifest.json", dir_fd=root_descriptor)
        _fsync(root_descriptor)
        os.close(root_descriptor)
        root_descriptor = None
        named_root = os.stat(
            cleanup_name,
            dir_fd=transactions_descriptor,
            follow_symlinks=False,
        )
        if _directory_identity(named_root) != validated.root_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "cleanup staging directory identity changed"
            )
        os.rmdir(cleanup_name, dir_fd=transactions_descriptor)
        _fsync(transactions_descriptor)
    except OSError as exc:
        raise LocalUnitOfWorkError(
            "stage-cleanup-failed", "verified staging cleanup failed"
        ) from exc
    finally:
        if objects_descriptor is not None:
            os.close(objects_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)
        os.close(transactions_descriptor)
    _fsync_directory(repository)


def _remove_incomplete_stage(
    repository: Path,
    transactions: Path,
    transactions_identity: tuple[int, int, int],
    root: Path,
    root_identity: tuple[int, int, int],
    objects_identity: tuple[int, int, int] | None,
    object_names: set[str],
    manifest_created: bool,
) -> None:
    """Best-effort cleanup for a partial private stage without recursive deletion."""
    transactions_descriptor = None
    root_descriptor = None
    objects_descriptor = None
    cleanup_name = ".cleanup-" + secrets.token_hex(16)
    try:
        transactions_descriptor, opened_transactions_identity = _open_directory(
            transactions,
            code="stage-directory-changed",
        )
        if opened_transactions_identity != transactions_identity:
            return
        root_descriptor = _open_child_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            root.name,
            root_identity,
            code="stage-directory-changed",
        )
        if objects_identity is not None:
            objects_descriptor = _open_child_directory(
                root_descriptor,
                root,
                root_identity,
                "objects",
                objects_identity,
                code="stage-directory-changed",
            )

        os.rename(
            root.name,
            cleanup_name,
            src_dir_fd=transactions_descriptor,
            dst_dir_fd=transactions_descriptor,
        )
        moved = os.stat(
            cleanup_name,
            dir_fd=transactions_descriptor,
            follow_symlinks=False,
        )
        if _directory_identity(moved) != root_identity:
            return

        if objects_descriptor is not None:
            for name in object_names:
                try:
                    os.unlink(name, dir_fd=objects_descriptor)
                except FileNotFoundError:
                    pass
            _fsync(objects_descriptor)
            os.close(objects_descriptor)
            objects_descriptor = None
            try:
                named_objects = os.stat(
                    "objects",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                named_objects = None
            if named_objects is not None:
                if _directory_identity(named_objects) != objects_identity:
                    return
                os.rmdir("objects", dir_fd=root_descriptor)
        if manifest_created:
            try:
                os.unlink("manifest.json", dir_fd=root_descriptor)
            except FileNotFoundError:
                pass
        _fsync(root_descriptor)
        os.close(root_descriptor)
        root_descriptor = None
        named_root = os.stat(
            cleanup_name,
            dir_fd=transactions_descriptor,
            follow_symlinks=False,
        )
        if _directory_identity(named_root) != root_identity:
            return
        os.rmdir(cleanup_name, dir_fd=transactions_descriptor)
        _fsync(transactions_descriptor)
    except (LocalUnitOfWorkError, OSError):
        return
    finally:
        if objects_descriptor is not None:
            os.close(objects_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)
        if transactions_descriptor is not None:
            os.close(transactions_descriptor)
    try:
        _fsync_directory(repository)
    except (LocalUnitOfWorkError, OSError):
        pass


def stage_generation(
    repository_root: Path | str,
    *,
    state_bytes: bytes,
    effects: tuple[BlobBinding, ...],
    blobs: VerifiedBlobSet,
) -> StagedGeneration:
    if type(state_bytes) is not bytes:
        raise LocalUnitOfWorkError(
            "immutable-input-required", "state generation must be immutable bytes"
        )
    if len(state_bytes) > STATE_LIMIT:
        raise LocalUnitOfWorkError("record-too-large", "state generation exceeds the state limit")
    decode_mission_state(state_bytes)
    _validate_blob_bindings(effects, blobs)
    repository = Path(repository_root)
    try:
        repository.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise LocalUnitOfWorkError(
            "repository-invalid", "repository cannot be created safely"
        ) from exc
    repository_metadata = _require_directory(repository, code="repository-invalid")
    transactions = repository / "transactions"
    transactions_metadata = _ensure_directory(
        transactions,
        mode=0o700,
        code="stage-directory-changed",
    )
    if transactions_metadata.st_dev != repository_metadata.st_dev:
        raise LocalUnitOfWorkError(
            "stage-filesystem-mismatch", "transaction directory must share the repository filesystem"
        )
    repository_identity = _directory_identity(repository_metadata)
    transactions_identity = _directory_identity(transactions_metadata)
    repository_descriptor, opened_repository_identity = _open_directory(
        repository, code="stage-directory-changed"
    )
    if opened_repository_identity != repository_identity:
        os.close(repository_descriptor)
        raise LocalUnitOfWorkError(
            "stage-directory-changed", "repository directory identity changed"
        )
    try:
        transactions_descriptor, opened_transactions_identity = _open_directory(
            transactions,
            code="stage-directory-changed",
        )
    except BaseException:
        os.close(repository_descriptor)
        raise
    if opened_transactions_identity != transactions_identity:
        os.close(transactions_descriptor)
        os.close(repository_descriptor)
        raise LocalUnitOfWorkError(
            "stage-directory-changed", "transaction directory identity changed"
        )
    root = None
    root_identity = None
    objects_identity = None
    object_names: set[str] = set()
    manifest_created = False
    try:
        root, root_identity = _create_private_stage_root(
            transactions,
            transactions_descriptor,
            transactions_identity,
        )
        objects = root / "objects"
        objects.mkdir(mode=0o700)
        objects_identity = _directory_identity(objects.lstat())
        object_names.add(_object_name(state_bytes))
        state_path = _stage_object(objects, state_bytes)
        blob_paths = []
        manifest_blobs = []
        for blob in blobs.blobs:
            object_names.add(_object_name(blob.content))
            path = _stage_object(objects, blob.content)
            blob_paths.append(path)
            manifest_blobs.append(
                {
                    "blob_id": blob.binding.blob_id,
                    "kind": blob.binding.kind,
                    "relative_path": blob.binding.relative_path,
                    "digest": blob.binding.digest,
                    "size": blob.binding.size,
                    "object": path.relative_to(root).as_posix(),
                }
            )
        manifest = {
            "schema": "mission-generation/1",
            "state": {
                "digest": "sha256:" + hashlib.sha256(state_bytes).hexdigest(),
                "size": len(state_bytes),
                "object": state_path.relative_to(root).as_posix(),
            },
            "blobs": manifest_blobs,
        }
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(manifest_bytes) > STATE_LIMIT:
            raise LocalUnitOfWorkError(
                "record-too-large", "generation manifest exceeds the manifest limit"
            )
        manifest_path = root / "manifest.json"
        manifest_created = True
        _write_private_file(manifest_path, manifest_bytes)
        _fsync_directory(objects)
        _fsync_directory(root)
        _verify_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            code="stage-directory-changed",
        )
        if _directory_identity(root.lstat()) != root_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            )
        _fsync(transactions_descriptor)
        _verify_directory(
            transactions_descriptor,
            transactions,
            transactions_identity,
            code="stage-directory-changed",
        )
        if _directory_identity(root.lstat()) != root_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            )
        _fsync_pinned_repository(
            repository,
            repository_descriptor,
            repository_identity,
            transactions,
            transactions_descriptor,
            transactions_identity,
            root,
            root_identity,
        )
        final_root_identity = _directory_identity(root.lstat())
        final_objects_identity = _directory_identity(objects.lstat())
        if final_root_identity != root_identity or final_objects_identity != objects_identity:
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            )
        state_identity = _file_identity(state_path.lstat())
        blob_identities = tuple(_file_identity(path.lstat()) for path in blob_paths)
        manifest_identity = _file_identity(manifest_path.lstat())
        _read_expected_file(
            state_path,
            state_bytes,
            code="staged-object-changed",
            required_mode=0o600,
            expected_identity=state_identity,
        )
        for path, content, identity in zip(blob_paths, blobs.blobs, blob_identities):
            _read_expected_file(
                path,
                content.content,
                code="staged-object-changed",
                required_mode=0o600,
                expected_identity=identity,
            )
        _read_expected_file(
            manifest_path,
            manifest_bytes,
            code="staged-manifest-changed",
            required_mode=0o600,
            expected_identity=manifest_identity,
        )
        if (
            _directory_identity(root.lstat()) != final_root_identity
            or _directory_identity(objects.lstat()) != final_objects_identity
        ):
            raise LocalUnitOfWorkError(
                "stage-directory-changed", "private stage identity changed"
            )
        return StagedGeneration(
            root=root,
            state_path=state_path,
            blob_paths=tuple(blob_paths),
            manifest_path=manifest_path,
            manifest_bytes=manifest_bytes,
            generation_digest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            root_identity=final_root_identity,
            objects_identity=final_objects_identity,
            state_identity=state_identity,
            blob_identities=blob_identities,
            manifest_identity=manifest_identity,
        )
    except BaseException:
        if root is not None and root_identity is not None:
            _remove_incomplete_stage(
                repository,
                transactions,
                transactions_identity,
                root,
                root_identity,
                objects_identity,
                object_names,
                manifest_created,
            )
        raise
    finally:
        os.close(transactions_descriptor)
        os.close(repository_descriptor)


def publish_generation(
    repository_root: Path | str,
    staged: StagedGeneration,
) -> PublishedGeneration:
    """Publish an unreferenced immutable generation; no session head is changed."""
    repository = Path(repository_root)
    validated = _validate_staged_generation(repository, staged)
    state_bytes = validated.state_bytes
    blob_bytes = validated.blob_bytes
    repository_metadata = repository.lstat()
    transactions = repository / "transactions"
    expected_generation_digest = "sha256:" + hashlib.sha256(staged.manifest_bytes).hexdigest()

    objects = repository / "objects"
    generations = repository / "generations"
    _ensure_directory(objects, mode=0o700, code="immutable-object-directory-invalid")
    _ensure_directory(generations, mode=0o700, code="generation-directory-invalid")
    if objects.stat().st_dev != repository_metadata.st_dev or generations.stat().st_dev != repository_metadata.st_dev:
        raise LocalUnitOfWorkError(
            "stage-filesystem-mismatch", "publication directories must share the repository filesystem"
        )

    published_paths = []
    published_contents = []
    manifest_name = expected_generation_digest.removeprefix("sha256:") + ".json"
    manifest_path = generations / manifest_name
    object_entries = [(objects / _object_name(state_bytes), state_bytes)]
    for content in blob_bytes:
        destination = objects / _object_name(content)
        if all(existing != destination for existing, _ in object_entries):
            object_entries.append((destination, content))

    if manifest_path.exists() or manifest_path.is_symlink():
        _read_expected_file(
            manifest_path,
            staged.manifest_bytes,
            code="immutable-generation-collision",
        )
        for path, content in object_entries:
            _read_expected_file(path, content, code="immutable-generation-changed")
        _remove_validated_stage(repository, staged, validated)
        return PublishedGeneration(
            manifest_path=manifest_path,
            object_paths=tuple(path for path, _ in object_entries),
            generation_digest=expected_generation_digest,
            reused=True,
        )

    with _PublishedLinksTransaction() as publication:
        state_destination, state_content = object_entries[0]
        _publish_immutable_file(
            staged.state_path,
            state_destination,
            state_content,
            expected_identity=staged.state_identity,
            publication=publication,
        )
        published_paths.append(state_destination)
        published_contents.append(state_content)
        for path, content, identity in zip(
            staged.blob_paths,
            blob_bytes,
            staged.blob_identities,
        ):
            destination = objects / _object_name(content)
            if destination not in published_paths:
                _publish_immutable_file(
                    path,
                    destination,
                    content,
                    expected_identity=identity,
                    publication=publication,
                )
                published_paths.append(destination)
                published_contents.append(content)
        manifest_created = (
            _publish_immutable_file(
                staged.manifest_path,
                manifest_path,
                staged.manifest_bytes,
                expected_identity=staged.manifest_identity,
                publication=publication,
            )
            is not None
        )
        _remove_validated_stage(repository, staged, validated)
        _read_expected_file(
            manifest_path,
            staged.manifest_bytes,
            code="immutable-generation-changed",
        )
        for path, content in zip(published_paths, published_contents):
            _read_expected_file(path, content, code="immutable-generation-changed")
    return PublishedGeneration(
        manifest_path=manifest_path,
        object_paths=tuple(published_paths),
        generation_digest=expected_generation_digest,
        reused=not manifest_created,
    )


def validate_staged_generation(
    repository_root: Path | str,
    staged: StagedGeneration,
) -> None:
    """Revalidate a private stage without publishing it.

    U2 uses this immediately before its fenced CAS prepare boundary.  Keeping
    the validation owner here avoids a second manifest/file-identity contract.
    """
    _validate_staged_generation(Path(repository_root), staged)


def load_staged_generation(
    repository_root: Path | str,
    transaction_id: str,
) -> StagedGeneration:
    """Reconstruct and validate one complete private stage after a process exit."""
    if not isinstance(transaction_id, str) or re.fullmatch(
        r"[0-9a-f]{32}", transaction_id
    ) is None:
        raise LocalUnitOfWorkError(
            "stage-invalid", "private stage transaction ID is invalid"
        )
    repository = Path(repository_root)
    root = repository / "transactions" / (".stage-" + transaction_id)
    manifest_path = root / "manifest.json"
    try:
        root_metadata = root.lstat()
        objects = root / "objects"
        objects_metadata = objects.lstat()
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or not stat.S_ISDIR(objects_metadata.st_mode)
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
            or stat.S_IMODE(objects_metadata.st_mode) != 0o700
        ):
            raise LocalUnitOfWorkError(
                "stage-invalid", "private stage directories are invalid"
            )
        manifest_bytes = read_stable_bytes(manifest_path, limit=STATE_LIMIT)
        frozen = decode_json_object(manifest_bytes, limit=STATE_LIMIT)
        if encode_json_object(frozen) != manifest_bytes:
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "manifest is not canonical JSON"
            )
        manifest = thaw_json_object(frozen)
        if (
            set(manifest) != {"schema", "state", "blobs"}
            or manifest.get("schema") != "mission-generation/1"
            or not isinstance(manifest.get("state"), dict)
            or not isinstance(manifest.get("blobs"), list)
        ):
            raise LocalUnitOfWorkError(
                "staged-manifest-changed", "manifest envelope differs"
            )

        def object_path(record: object) -> Path:
            if not isinstance(record, dict):
                raise LocalUnitOfWorkError(
                    "staged-manifest-changed", "manifest object record differs"
                )
            digest = record.get("digest")
            reference = record.get("object")
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                or reference
                != "objects/" + digest.removeprefix("sha256:") + ".blob"
            ):
                raise LocalUnitOfWorkError(
                    "staged-manifest-changed", "manifest object path differs"
                )
            return root / reference

        state_path = object_path(manifest["state"])
        blob_paths = tuple(object_path(record) for record in manifest["blobs"])
        staged = StagedGeneration(
            root=root,
            state_path=state_path,
            blob_paths=blob_paths,
            manifest_path=manifest_path,
            manifest_bytes=manifest_bytes,
            generation_digest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            root_identity=_directory_identity(root_metadata),
            objects_identity=_directory_identity(objects_metadata),
            state_identity=_file_identity(state_path.lstat()),
            blob_identities=tuple(_file_identity(path.lstat()) for path in blob_paths),
            manifest_identity=_file_identity(manifest_path.lstat()),
        )
        _validate_staged_generation(repository, staged)
        return staged
    except LocalUnitOfWorkError:
        raise
    except (OSError, StrictReadError, KeyError, TypeError, ValueError) as exc:
        raise LocalUnitOfWorkError(
            "stage-invalid", "private stage cannot be reconstructed safely"
        ) from exc


def validate_verified_blob_set(blobs: VerifiedBlobSet) -> None:
    """Validate one immutable captured blob set without staging it."""
    if not isinstance(blobs, VerifiedBlobSet) or type(blobs.blobs) is not tuple:
        raise LocalUnitOfWorkError(
            "immutable-input-required", "verified blobs must be immutable"
        )
    bindings = []
    for blob in blobs.blobs:
        if not isinstance(blob, VerifiedBlob):
            raise LocalUnitOfWorkError(
                "blob-binding-mismatch", "captured blob type is invalid"
            )
        bindings.append(blob.binding)
    _validate_blob_bindings(tuple(bindings), blobs)


def discard_staged_generation(
    repository_root: Path | str,
    staged: StagedGeneration,
) -> None:
    """Remove one still-valid private stage without touching public objects."""
    repository = Path(repository_root)
    validated = _validate_staged_generation(repository, staged)
    _remove_validated_stage(repository, staged, validated)
