"""Auditable terminal-session reinitialization for the v5 repository."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from mission_application.lifecycle import reinitialized_assumptions_path
from mission_persistence.administrative import (
    AdministrativeCommitError,
    publish_administrative_generation,
)
from mission_persistence.aggregate_index import RecoverableAggregateIndex
from mission_persistence.authoritative_reader import (
    AuthoritativeSnapshot,
    authoritative_snapshot_from_validated_archive_bytes,
)
from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository
from mission_persistence.strict_reader import (
    STATE_LIMIT,
    read_stable_bytes_beneath,
)
from mission_kernel.errors import StrictReadError


ARCHIVE_SCHEMA = "mission-session-archive/1"
RESERVATION_SCHEMA = "mission-new-assumptions-reservation/1"
_ASSUMPTIONS_BYTES = b"# Assumption Registry\n"


def _canonical_bytes(document: dict) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

@dataclass(frozen=True)
class CurrentMission:
    snapshot: AuthoritativeSnapshot
    head_digest: str

    def document_copy(self) -> dict:
        return self.snapshot.document_copy()


@dataclass(frozen=True)
class ArchivedMission:
    state_path: Path
    assumptions_relative: str | None
    assumptions_bytes: bytes | None
    assumptions_identity: tuple[tuple[int, ...], ...] | None


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


class V5MissionReinitializer:
    """Bridge application admission to immutable archive and fenced UOW commit."""

    def __init__(
        self,
        root: Path,
        state_path: Path,
        initialize_new_session: Callable[[object, Path], None],
    ) -> None:
        self.root = Path(root)
        self.state_path = Path(state_path)
        self.session_id = self.state_path.stem
        self._initialize_new_session = initialize_new_session
        self._reservation_lock_fd = None

    def initialize(self, arguments: object) -> None:
        raise FencedCommitError(
            "session-already-initialized", "session-already-initialized"
        )

    def current_mission(self) -> CurrentMission:
        repository = LocalFencedRepository(self.root / ".mission-state")
        repository_snapshot = repository.read(self.session_id)
        snapshot = authoritative_snapshot_from_validated_archive_bytes(
            repository_snapshot.state_bytes,
            expected_session_id=self.session_id,
        )
        return CurrentMission(snapshot, repository_snapshot.result.head_digest)

    def _reserve_new_assumptions(
        self, relative_path: str, head_digest: str
    ) -> tuple[Path, tuple]:
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.parts[:2] != (".mission-state", "sessions")
            or len(relative.parts) != 3
        ):
            raise FencedCommitError(
                "new-mission-assumptions-invalid",
                "new assumptions path is outside the session repository",
            )
        marker_name = relative.parts[-1] + ".reservation"
        marker_payload = _canonical_bytes(
            {
                "schema": RESERVATION_SCHEMA,
                "session_id": self.session_id,
                "head_digest": head_digest,
                "assumptions_path": relative.as_posix(),
            }
        )
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptors = []
        relationships = []
        target_created = marker_created = False
        try:
            root_fd = os.open(os.fspath(self.root), directory_flags)
            descriptors.append(root_fd)
            parent_fd = root_fd
            for part in relative.parts[:-1]:
                child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
                descriptors.append(child_fd)
                relationships.append((parent_fd, part, child_fd))
                parent_fd = child_fd

            def verify_parents() -> None:
                for parent, name, child in relationships:
                    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (
                        _directory_identity(named)
                        != _directory_identity(os.fstat(child))
                    ):
                        raise OSError("assumptions parent identity changed")

            def write_exclusive(name: str, payload: bytes) -> tuple[tuple, bool]:
                descriptor = os.open(
                    name,
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
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise OSError("assumptions reservation identity is unsafe")
                return _file_identity(metadata), True

            try:
                marker_capture = read_stable_bytes_beneath(
                    self.root,
                    "/".join(relative.parts[:-1] + (marker_name,)),
                    limit=STATE_LIMIT,
                )
            except StrictReadError:
                marker_capture = None
            if marker_capture is None:
                _marker_identity, marker_created = write_exclusive(
                    marker_name, marker_payload
                )
            elif marker_capture.payload != marker_payload:
                raise FileExistsError("assumptions reservation belongs to another operation")

            lock_fd = os.open(
                marker_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(lock_fd)
                raise FileExistsError("new assumptions reservation is active") from exc
            self._reservation_lock_fd = lock_fd

            try:
                target_capture = read_stable_bytes_beneath(
                    self.root, relative.as_posix(), limit=STATE_LIMIT
                )
            except StrictReadError:
                target_capture = None
            if target_capture is None:
                identity, target_created = write_exclusive(
                    relative.parts[-1], _ASSUMPTIONS_BYTES
                )
            elif (
                marker_capture is not None
                and target_capture.payload == _ASSUMPTIONS_BYTES
            ):
                identity = target_capture.identity[-1]
            else:
                raise FileExistsError("new assumptions record has unexpected content")
            verify_parents()
            os.fsync(parent_fd)
            verify_parents()
        except OSError as exc:
            self._release_reservation()
            if target_created:
                try:
                    os.unlink(relative.parts[-1], dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError:
                    pass
            if marker_created:
                try:
                    os.unlink(marker_name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError:
                    pass
            if isinstance(exc, FileExistsError):
                raise FencedCommitError(
                    "new-mission-assumptions-exists",
                    "new-mission-assumptions-exists: new assumptions record cannot "
                    "be reserved exclusively",
                ) from exc
            raise FencedCommitError(
                "new-mission-assumptions-invalid",
                "new-mission-assumptions-invalid: new assumptions record cannot be "
                "reserved safely",
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return self.root.joinpath(*relative.parts), identity

    def _release_reservation(self) -> None:
        if self._reservation_lock_fd is not None:
            try:
                os.close(self._reservation_lock_fd)
            except OSError:
                pass
            self._reservation_lock_fd = None

    @staticmethod
    def _cleanup_reserved_assumptions(target: Path, identity: tuple) -> None:
        try:
            metadata = target.lstat()
            current = _file_identity(metadata)
            if current != identity:
                return
            target.unlink()
            descriptor = os.open(
                os.fspath(target.parent),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            return

    def _archive_current(self, current: CurrentMission) -> ArchivedMission:
        snapshot = current.snapshot
        state_bytes = snapshot.state_bytes
        document = snapshot.document_copy()
        mission_id = str(document.get("mission_id") or "")
        if not mission_id:
            raise FencedCommitError(
                "archive-state-invalid", "terminal session has no mission_id"
            )
        bundle_name = "session-" + hashlib.sha256(
            self.session_id.encode("utf-8")
        ).hexdigest()[:16]
        bundle = self.root / ".mission-state" / "archive" / bundle_name

        state_digest = hashlib.sha256(state_bytes).hexdigest()
        state_relative = Path("sessions") / (self.session_id + ".json")
        files = {state_relative.as_posix(): state_bytes}
        core = {
            "schema": ARCHIVE_SCHEMA,
            "session_id": self.session_id,
            "mission_id": mission_id,
            "iteration": snapshot.iteration,
            "state": {
                "path": state_relative.as_posix(),
                "sha256": state_digest,
                "size": len(state_bytes),
            },
        }
        assumptions_path = document.get("assumptions_path")
        if assumptions_path:
            relative = PurePosixPath(str(assumptions_path))
            if (
                relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
                or not relative.parts
                or relative.parts[0] != ".mission-state"
            ):
                raise FencedCommitError(
                    "archive-assumptions-invalid",
                    "terminal assumptions path is outside the mission repository",
                )
            try:
                assumptions_capture = read_stable_bytes_beneath(
                    self.root,
                    relative.as_posix(),
                    limit=STATE_LIMIT,
                )
                assumptions_bytes = assumptions_capture.payload
            except (OSError, StrictReadError) as exc:
                raise FencedCommitError(
                    "archive-assumptions-unreadable",
                    "archive-assumptions-unreadable: terminal assumptions cannot be "
                    "captured safely",
                ) from exc
            archived_assumptions = Path("assumptions") / relative.parts[-1]
            assumptions_digest = hashlib.sha256(assumptions_bytes).hexdigest()
            core["assumptions"] = {
                "source_path": relative.as_posix(),
                "path": archived_assumptions.as_posix(),
                "sha256": assumptions_digest,
                "size": len(assumptions_bytes),
            }
            files[archived_assumptions.as_posix()] = assumptions_bytes
        generation = hashlib.sha256(_canonical_bytes(core)).hexdigest()
        manifest = {**core, "content_digest": generation}
        files["manifest.json"] = _canonical_bytes(manifest)
        try:
            generation_root = publish_administrative_generation(
                bundle,
                generation=generation,
                files=files,
            )
        except AdministrativeCommitError as exc:
            raise FencedCommitError(exc.code, exc.detail) from exc
        archived_to = generation_root / state_relative
        return ArchivedMission(
            archived_to,
            relative.as_posix() if assumptions_path else None,
            assumptions_bytes if assumptions_path else None,
            assumptions_capture.identity if assumptions_path else None,
        )

    def start_new_mission(
        self, arguments: object, current: CurrentMission
    ) -> None:
        assumptions_relative = reinitialized_assumptions_path(
            self.session_id, current.head_digest
        )
        assumptions_target, assumptions_identity = self._reserve_new_assumptions(
            assumptions_relative, current.head_digest
        )
        try:
            archived = self._archive_current(current)
        except BaseException:
            self._cleanup_reserved_assumptions(
                assumptions_target, assumptions_identity
            )
            self._release_reservation()
            raise
        archived_to = archived.state_path
        new_state_initialized = False
        try:
            if archived.assumptions_relative is not None:
                recaptured = read_stable_bytes_beneath(
                    self.root,
                    archived.assumptions_relative,
                    limit=STATE_LIMIT,
                )
                if (
                    recaptured.payload != archived.assumptions_bytes
                    or recaptured.identity != archived.assumptions_identity
                ):
                    raise FencedCommitError(
                        "archive-assumptions-changed",
                        "terminal assumptions changed before repository reset",
                    )
            coordinator = RecoverableAggregateIndex(
                self.root / ".mission-state",
                session_id=self.session_id,
                authority_format="v5",
            )
            coordinator.recover()
            intent = coordinator.prepare("add")
            setattr(arguments, "_new_mission_expected_head_digest", current.head_digest)
            setattr(
                arguments,
                "_new_mission_assumptions_path",
                assumptions_relative,
            )
            setattr(arguments, "_new_mission_authority_committed", False)
            output = io.StringIO()
            try:
                try:
                    with contextlib.redirect_stdout(output):
                        self._initialize_new_session(arguments, self.root)
                except BaseException:
                    captured = output.getvalue()
                    if captured:
                        sys.stdout.write(captured)
                    try:
                        new_state_initialized = (
                            LocalFencedRepository(
                                self.root / ".mission-state"
                            ).read(self.session_id).result.head_digest
                            != current.head_digest
                        )
                    except (FencedCommitError, OSError):
                        new_state_initialized = False
                    coordinator.finalize(intent)
                    raise
                coordinator.finalize(intent)
            finally:
                new_state_initialized = new_state_initialized or bool(
                    getattr(arguments, "_new_mission_authority_committed", False)
                )
                delattr(arguments, "_new_mission_authority_committed")
                delattr(arguments, "_new_mission_assumptions_path")
                delattr(arguments, "_new_mission_expected_head_digest")
        except BaseException:
            if not new_state_initialized:
                self._cleanup_reserved_assumptions(
                    assumptions_target, assumptions_identity
                )
            print(
                json.dumps(
                    {"ok": False, "archived_to": str(archived_to)},
                    ensure_ascii=False,
                )
            )
            raise
        finally:
            self._release_reservation()
        try:
            result = json.loads(output.getvalue())
        except json.JSONDecodeError as exc:
            raise FencedCommitError(
                "new-mission-result-invalid",
                "new mission initializer did not return one JSON result",
            ) from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise FencedCommitError(
                "new-mission-result-invalid",
                "new mission initializer returned an invalid result",
            )
        result["archived_to"] = str(archived_to)
        print(json.dumps(result, ensure_ascii=False))
