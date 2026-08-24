"""Recoverable publication protocol for the rebuildable aggregate index."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from mission_kernel.json_codec import decode_json_object, thaw_json_object

from .authoritative_reader import read_authoritative_snapshot
from .repository_binding import RepositoryFormat, inspect_repository_bytes
from .strict_reader import read_stable_bytes


INTENT_SCHEMA = "mission-aggregate-index-intent/1"
INTENT_LIMIT = 16 * 1024
AGGREGATE_LIMIT = 4 * 1024 * 1024
_VALID_FORMATS = frozenset({"legacy-v4", "v5"})
_VALID_ACTIONS = frozenset({"add", "remove"})


class AggregateIndexProtocolError(Exception):
    """Protocol failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


class _AggregateRebuildRequired(Exception):
    """Internal retry signal when aggregate.json disappears before locking."""


@dataclass(frozen=True)
class AggregateIntent:
    path: Path
    session_id: str
    action: str
    authority_format: str
    base_authority_digest: str
    created_at: str
    payload: bytes
    identity: tuple


@dataclass(frozen=True)
class _AuthorityCapture:
    session_id: str
    authority_format: str
    digest: str
    active: bool
    path: Path
    raw_identity: tuple
    raw_digest: str


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_utc_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _intent_name(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json"


def _valid_session_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 256
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\0" not in value
    )


def _active_membership(document: dict) -> bool:
    return (
        document.get("loop_active") is True
        and document.get("passes") is not True
        and not document.get("halt_reason")
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(path: Path):
    metadata = path.lstat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _path_entry_exists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _require_plain_directory(path: Path, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AggregateIndexProtocolError(code, "directory is unreadable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise AggregateIndexProtocolError(code, "directory must not be a symlink")


def _atomic_publish(path: Path, payload: bytes, *, expected_identity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=os.fspath(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current_identity = _identity(path) if _path_entry_exists(path) else None
        if current_identity != expected_identity:
            raise AggregateIndexProtocolError(
                "record-changed", "record changed before atomic publication"
            )
        os.replace(os.fspath(temporary), os.fspath(path))
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class RecoverableAggregateIndex:
    """Coordinate durable intent, authoritative save, and index reconciliation."""

    def __init__(
        self,
        state_root: Path,
        *,
        session_id: Optional[str] = None,
        authority_format: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
        fault_injector: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._root = Path(state_root)
        self._session_id = session_id
        self._authority_format = authority_format
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fault_injector = fault_injector
        self._active_inflight: dict[str, int] = {}
        if session_id is not None and not _valid_session_id(session_id):
            raise ValueError("session id is invalid")
        if authority_format is not None and authority_format not in _VALID_FORMATS:
            raise ValueError("authority format is invalid")

    @property
    def intent_directory(self) -> Path:
        return self._root / "aggregate-index-intents"

    @property
    def aggregate_path(self) -> Path:
        return self._root / "aggregate.json"

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def _inflight_path(self, session_id: str) -> Path:
        return self.intent_directory / (_intent_name(session_id)[:-5] + ".lock")

    def _acquire_inflight(self, session_id: str, *, blocking: bool) -> Optional[int]:
        path = self._inflight_path(session_id)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(os.fspath(path), flags, 0o600)
        except OSError as exc:
            raise AggregateIndexProtocolError("inflight-lock-unavailable", str(exc)) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AggregateIndexProtocolError(
                    "inflight-lock-identity-invalid",
                    "inflight lock must be a regular single-link file",
                )
            operation = fcntl.LOCK_EX
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError:
                os.close(descriptor)
                return None
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _release_inflight(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _release_active_inflight(self, session_id: str) -> None:
        descriptor = self._active_inflight.pop(session_id, None)
        if descriptor is not None:
            self._release_inflight(descriptor)

    @contextlib.contextmanager
    def _locked(self):
        self._root.mkdir(parents=True, exist_ok=True)
        lock_path = self._root / ".aggregate-index.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(os.fspath(lock_path), flags, 0o600)
        except OSError as exc:
            raise AggregateIndexProtocolError("lock-unavailable", str(exc)) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AggregateIndexProtocolError(
                    "lock-identity-invalid", "aggregate lock must be a regular single-link file"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _authority_path(self, session_id: str) -> Path:
        sessions = self._root / "sessions"
        if _path_entry_exists(sessions):
            _require_plain_directory(sessions, "authority-directory-invalid")
        session_path = sessions / (session_id + ".json")
        if _path_entry_exists(session_path):
            return session_path
        legacy_path = self._root / "state.json"
        if _path_entry_exists(legacy_path):
            return legacy_path
        raise AggregateIndexProtocolError(
            "authority-unreadable", "authoritative session is missing"
        )

    def _capture_authority(
        self, session_id: str, expected_format: Optional[str]
    ) -> _AuthorityCapture:
        path = self._authority_path(session_id)
        return self._capture_authority_path(path, session_id, expected_format)

    def _capture_authority_path(
        self,
        path: Path,
        session_id: str,
        expected_format: Optional[str],
    ) -> _AuthorityCapture:
        try:
            source = read_stable_bytes(path)
            raw_identity = _identity(path)
            inspected = inspect_repository_bytes(source, expected_session_id=session_id)
            actual_format = inspected.format.value
            if expected_format is not None and actual_format != expected_format:
                raise AggregateIndexProtocolError(
                    "authority-format-changed", "authority format differs from intent"
                )
            if inspected.format is RepositoryFormat.LEGACY_V4:
                document = thaw_json_object(inspected.document)
                identity_material = source
            else:
                snapshot = read_authoritative_snapshot(
                    path, expected_session_id=session_id
                )
                if read_stable_bytes(path) != source:
                    raise AggregateIndexProtocolError(
                        "authority-changed", "authority changed during capture"
                    )
                document = snapshot.document_copy()
                identity_material = (
                    source
                    + b"\0"
                    + snapshot.state_bytes
                    + b"\0"
                    + str(snapshot.generation).encode("ascii")
                    + b"\0"
                    + str(snapshot.commit_digest).encode("ascii")
                )
        except AggregateIndexProtocolError:
            raise
        except Exception as exc:
            raise AggregateIndexProtocolError("authority-unreadable", str(exc)) from exc
        return _AuthorityCapture(
            session_id,
            actual_format,
            _digest(identity_material),
            _active_membership(document),
            path,
            raw_identity,
            _digest(source),
        )

    @staticmethod
    def _authority_unchanged(authority: _AuthorityCapture) -> bool:
        """Recheck only the captured session/head bytes while holding index lock."""
        try:
            source = read_stable_bytes(authority.path)
            return (
                _identity(authority.path) == authority.raw_identity
                and _digest(source) == authority.raw_digest
            )
        except Exception:
            return False

    def _decode_intent(self, path: Path) -> AggregateIntent:
        try:
            initial_identity = _identity(path)
            payload = read_stable_bytes(path, limit=INTENT_LIMIT)
            final_identity = _identity(path)
            if final_identity != initial_identity:
                raise AggregateIndexProtocolError(
                    "intent-changed", "intent changed during capture"
                )
            document = thaw_json_object(decode_json_object(payload))
        except Exception as exc:
            raise AggregateIndexProtocolError("intent-invalid", str(exc)) from exc
        if not isinstance(document, dict) or set(document) != {
            "schema",
            "session_id",
            "action",
            "authority_format",
            "base_authority_digest",
            "created_at",
        }:
            raise AggregateIndexProtocolError("intent-invalid", "intent fields are invalid")
        session_id = document.get("session_id")
        action = document.get("action")
        authority_format = document.get("authority_format")
        base_digest = document.get("base_authority_digest")
        created_at = document.get("created_at")
        if (
            document.get("schema") != INTENT_SCHEMA
            or not _valid_session_id(session_id)
            or action not in _VALID_ACTIONS
            or authority_format not in _VALID_FORMATS
            or not isinstance(base_digest, str)
            or len(base_digest) != 71
            or not base_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in base_digest[7:])
            or not _valid_utc_text(created_at)
            or path.name != _intent_name(session_id)
        ):
            raise AggregateIndexProtocolError("intent-invalid", "intent values are invalid")
        return AggregateIntent(
            path,
            session_id,
            action,
            authority_format,
            base_digest,
            created_at,
            payload,
            initial_identity,
        )

    def prepare(self, action: str) -> AggregateIntent:
        if self._session_id is None or self._authority_format is None:
            raise AggregateIndexProtocolError(
                "session-unbound", "prepare requires a session-bound coordinator"
            )
        if action not in _VALID_ACTIONS:
            raise ValueError("unknown aggregate action")
        authority = self._capture_authority(self._session_id, self._authority_format)
        created_at = _utc_text(self._clock())
        document = {
            "schema": INTENT_SCHEMA,
            "session_id": self._session_id,
            "action": action,
            "authority_format": self._authority_format,
            "base_authority_digest": authority.digest,
            "created_at": created_at,
        }
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        path = self.intent_directory / _intent_name(self._session_id)
        inflight = None
        with self._locked():
            if _path_entry_exists(self.intent_directory):
                _require_plain_directory(
                    self.intent_directory, "intent-directory-invalid"
                )
            else:
                self.intent_directory.mkdir(parents=True)
                _fsync_directory(self._root)
            if _path_entry_exists(path):
                raise AggregateIndexProtocolError(
                    "intent-pending", "session already has a pending aggregate intent"
                )
            inflight = self._acquire_inflight(self._session_id, blocking=True)
            assert inflight is not None
            try:
                _atomic_publish(path, payload, expected_identity=None)
            except BaseException:
                self._release_inflight(inflight)
                raise
        self._active_inflight[self._session_id] = inflight
        try:
            self._fault("after-intent-publish")
            return self._decode_intent(path)
        except BaseException:
            self._release_active_inflight(self._session_id)
            raise

    def _load_aggregate(self) -> tuple[dict, Optional[tuple]]:
        path = self.aggregate_path
        if not _path_entry_exists(path):
            return {}, None
        try:
            identity = _identity(path)
            if not stat.S_ISREG(identity[2]) or identity[3] != 1:
                raise AggregateIndexProtocolError(
                    "aggregate-identity-invalid",
                    "aggregate index must be a regular single-link file",
                )
            payload = read_stable_bytes(path, limit=AGGREGATE_LIMIT)
            document = thaw_json_object(decode_json_object(payload))
        except AggregateIndexProtocolError:
            raise
        except Exception as exc:
            raise AggregateIndexProtocolError("aggregate-invalid", str(exc)) from exc
        if not isinstance(document, dict):
            raise AggregateIndexProtocolError(
                "aggregate-invalid", "aggregate index must be an object"
            )
        sessions = document.get("active_sessions", [])
        if not isinstance(sessions, list) or any(
            not isinstance(value, str) for value in sessions
        ):
            raise AggregateIndexProtocolError(
                "aggregate-invalid", "active_sessions must be a string array"
            )
        if len(sessions) != len(set(sessions)):
            raise AggregateIndexProtocolError(
                "aggregate-invalid", "active_sessions contains duplicates"
            )
        return document, identity

    def _aggregate_fingerprint(self) -> tuple[tuple, str]:
        try:
            initial_identity = _identity(self.aggregate_path)
            payload = read_stable_bytes(
                self.aggregate_path, limit=AGGREGATE_LIMIT
            )
            if _identity(self.aggregate_path) != initial_identity:
                raise AggregateIndexProtocolError(
                    "aggregate-changed", "aggregate changed during verification"
                )
        except AggregateIndexProtocolError:
            raise
        except Exception as exc:
            raise AggregateIndexProtocolError(
                "aggregate-changed", "aggregate is unavailable after publication"
            ) from exc
        return initial_identity, _digest(payload)

    def _publish_membership(
        self,
        authority: _AuthorityCapture,
        *,
        rebuild_membership: Optional[list[str]] = None,
    ) -> tuple[tuple, str]:
        document, identity = self._load_aggregate()
        if identity is None:
            if rebuild_membership is None:
                raise _AggregateRebuildRequired()
            sessions = list(rebuild_membership)
            changed = True
        else:
            sessions = list(document.get("active_sessions", []))
            changed = False
            if authority.active and authority.session_id not in sessions:
                sessions.append(authority.session_id)
                changed = True
            elif not authority.active and authority.session_id in sessions:
                sessions.remove(authority.session_id)
                changed = True
        if changed:
            document["active_sessions"] = sessions
            document["updated_at"] = _utc_text(self._clock())
            payload = json.dumps(
                document,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            _atomic_publish(self.aggregate_path, payload, expected_identity=identity)
        published = self._aggregate_fingerprint()
        self._fault("after-index-publish")
        if self._aggregate_fingerprint() != published:
            raise AggregateIndexProtocolError(
                "aggregate-changed", "aggregate changed after publication"
            )
        return published

    def _consume(
        self, intent: AggregateIntent, aggregate_fingerprint: tuple[tuple, str]
    ) -> None:
        if self._aggregate_fingerprint() != aggregate_fingerprint:
            raise AggregateIndexProtocolError(
                "aggregate-changed", "aggregate changed before intent consumption"
            )
        current = self._decode_intent(intent.path)
        if current.payload != intent.payload or current.identity != intent.identity:
            raise AggregateIndexProtocolError(
                "intent-changed", "pending intent changed before consumption"
            )
        self._fault("before-intent-remove")
        if self._aggregate_fingerprint() != aggregate_fingerprint:
            raise AggregateIndexProtocolError(
                "aggregate-changed", "aggregate changed before intent removal"
            )
        if _identity(intent.path) != intent.identity:
            raise AggregateIndexProtocolError(
                "intent-changed", "pending intent changed before removal"
            )
        intent.path.unlink()
        _fsync_directory(intent.path.parent)

    def _reconcile(self, intent: AggregateIntent) -> None:
        for _attempt in range(3):
            captured = self._capture_authority(
                intent.session_id, intent.authority_format
            )
            rebuild_captures = (
                self._desired_captures()
                if not _path_entry_exists(self.aggregate_path)
                else None
            )
            with self._locked():
                if not self._authority_unchanged(captured) or (
                    rebuild_captures is not None
                    and not all(
                        self._authority_unchanged(item)
                        for item in rebuild_captures
                    )
                ):
                    continue
                try:
                    aggregate_fingerprint = self._publish_membership(
                        captured,
                        rebuild_membership=(
                            [
                                item.session_id
                                for item in rebuild_captures
                                if item.active
                            ]
                            if rebuild_captures is not None
                            else None
                        ),
                    )
                except _AggregateRebuildRequired:
                    continue
                self._consume(intent, aggregate_fingerprint)
                return
        raise AggregateIndexProtocolError(
            "authority-changing", "authority did not stabilize during reconciliation"
        )

    def finalize(self, intent: AggregateIntent) -> None:
        owned = intent.session_id in self._active_inflight
        descriptor = None
        if not owned:
            descriptor = self._acquire_inflight(intent.session_id, blocking=True)
            assert descriptor is not None
        try:
            self._reconcile(intent)
        finally:
            if owned:
                self._release_active_inflight(intent.session_id)
            elif descriptor is not None:
                self._release_inflight(descriptor)

    def recover(self) -> int:
        directory = self.intent_directory
        if not _path_entry_exists(directory):
            return 0
        _require_plain_directory(directory, "intent-directory-invalid")
        recovered = 0
        for path in sorted(directory.glob("*.json")):
            intent = self._decode_intent(path)
            owned = intent.session_id in self._active_inflight
            descriptor = None
            if not owned:
                descriptor = self._acquire_inflight(
                    intent.session_id, blocking=False
                )
                if descriptor is None:
                    continue
            try:
                self._reconcile(intent)
                recovered += 1
            finally:
                if owned:
                    self._release_active_inflight(intent.session_id)
                elif descriptor is not None:
                    self._release_inflight(descriptor)
        return recovered

    def _authority_paths(self) -> Iterable[Path]:
        legacy = self._root / "state.json"
        if _path_entry_exists(legacy):
            yield legacy
        sessions = self._root / "sessions"
        if _path_entry_exists(sessions):
            _require_plain_directory(sessions, "authority-directory-invalid")
            yield from sorted(sessions.glob("*.json"))

    def _desired_captures(self) -> list[_AuthorityCapture]:
        captures = []
        seen = set()
        for path in self._authority_paths():
            try:
                session_id = path.stem
                source = read_stable_bytes(path)
                inspected = inspect_repository_bytes(
                    source,
                    expected_session_id=(
                        None if path.name == "state.json" else session_id
                    ),
                )
            except Exception as exc:
                raise AggregateIndexProtocolError(
                    "authority-unreadable", "authoritative session cannot be inspected"
                ) from exc
            embedded = inspected.document_session
            if path.name == "state.json" and embedded:
                session_id = embedded
            if session_id in seen:
                raise AggregateIndexProtocolError(
                    "authority-duplicated",
                    "multiple authoritative records claim one session id",
                )
            seen.add(session_id)
            capture = self._capture_authority_path(
                path, session_id, inspected.format.value
            )
            captures.append(capture)
        return captures

    def _desired_membership(self) -> list[str]:
        return [
            capture.session_id
            for capture in self._desired_captures()
            if capture.active
        ]

    def repair(self, *, execute: bool = False) -> dict:
        desired = self._desired_membership()
        try:
            current, identity = self._load_aggregate()
            valid = True
            matches = current.get("active_sessions", []) == desired
        except AggregateIndexProtocolError:
            current, identity = (
                {},
                _identity(self.aggregate_path)
                if _path_entry_exists(self.aggregate_path)
                else None,
            )
            valid = False
            matches = False
        if _path_entry_exists(self.intent_directory):
            _require_plain_directory(
                self.intent_directory, "intent-directory-invalid"
            )
            pending_paths = list(self.intent_directory.glob("*.json"))
            if execute:
                for path in pending_paths:
                    self._decode_intent(path)
            pending = len(pending_paths)
        else:
            pending = 0
        if execute:
            for _attempt in range(3):
                captures = self._desired_captures()
                desired = [
                    capture.session_id for capture in captures if capture.active
                ]
                with self._locked():
                    if not all(
                        self._authority_unchanged(capture)
                        for capture in captures
                    ):
                        continue
                    try:
                        current, identity = self._load_aggregate()
                        valid = True
                        matches = current.get("active_sessions", []) == desired
                    except AggregateIndexProtocolError:
                        identity = (
                            _identity(self.aggregate_path)
                            if _path_entry_exists(self.aggregate_path)
                            else None
                        )
                        valid = False
                        matches = False
                    if not valid or not matches:
                        document = {
                            "active_sessions": desired,
                            "updated_at": _utc_text(self._clock()),
                        }
                        payload = json.dumps(
                            document, ensure_ascii=False, indent=2
                        ).encode("utf-8")
                        _atomic_publish(
                            self.aggregate_path, payload, expected_identity=identity
                        )
                        valid = True
                        matches = True
                    break
            else:
                raise AggregateIndexProtocolError(
                    "authority-changing",
                    "authority did not stabilize during aggregate repair",
                )
            self.recover()
            desired = self._desired_membership()
            try:
                current, _identity_after_recovery = self._load_aggregate()
                valid = True
                matches = current.get("active_sessions", []) == desired
            except AggregateIndexProtocolError:
                valid = False
                matches = False
            if _path_entry_exists(self.intent_directory):
                pending = len(list(self.intent_directory.glob("*.json")))
            else:
                pending = 0
        return {
            "valid": valid,
            "matches_authority": matches,
            "active_sessions": desired,
            "pending_intents": pending,
            "executed": execute,
        }
