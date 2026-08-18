"""Fenced generation CAS and immutable commit/head records for v5 repositories."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Union

from mission_kernel import decode_mission_state, decode_snapshot, project_legacy_document
from mission_kernel.codec_v5 import encode_v5_state
from mission_kernel.json_codec import (
    STATE_LIMIT,
    decode_json_object,
    encode_json_object,
    thaw_json_object,
)
from mission_kernel.model import (
    FencedLease,
    FrozenJsonObject,
    LeaseHistoryEntry,
    LegacyAbsentLease,
    MissionState,
    SchemaOrigin,
)
from mission_kernel.commands import encode_kernel_command, kernel_command_type
from mission_kernel.transitions import (
    bind_transition_effects,
    Decision,
    Transition,
    decide,
    is_sealed_transition,
    is_transition_bound_to,
)
from mission_application.ports import (
    AuditMetadata,
    CommitResult,
    ExecutionRequest,
    RepositoryExecutionResult,
    VerifiedBlobSetView,
)

from .local_uow import (
    MAX_BLOB_COUNT,
    MAX_TOTAL_BLOB_BYTES,
    BlobBinding,
    LocalUnitOfWorkError,
    PublishedGeneration,
    StagedGeneration,
    VerifiedBlobSet,
    discard_staged_generation,
    load_staged_generation,
    publish_generation,
    stage_generation,
    validate_staged_generation,
    validate_verified_blob_set,
)
from .gc import (
    GCReport,
    GarbageCollectionError,
    RetentionPolicy,
    collect_locked,
)
MAX_HEAD_BYTES = 4 * 1024
MAX_AUDIT_BYTES = 8 * 1024
MAX_COMMIT_BYTES = STATE_LIMIT
MAX_PREPARE_BYTES = STATE_LIMIT
MAX_OPERATION_BYTES = 4 * 1024
MAX_AUDIT_EVENT_TYPES = MAX_BLOB_COUNT
DEFAULT_LEASE_TTL_SECONDS = 15 * 60

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_TRANSACTION_RE = re.compile(r"[0-9a-f]{32}")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class FencedCommitError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AuditRecord:
    command_type_digest: str
    event_type_digests: tuple[str, ...]


@dataclass(frozen=True)
class RecordRef:
    digest: str
    path: str
    size: int


@dataclass(frozen=True)
class EffectRef:
    blob_id: str
    digest: str
    kind: str
    object: str
    relative_path: str
    size: int


@dataclass(frozen=True)
class BaseRef:
    generation: int
    head_digest: Optional[str]


@dataclass(frozen=True)
class HeadRecord:
    commit: RecordRef
    generation: int
    session_id: str
    state_generation: RecordRef
    schema: str = "mission-head/1"


@dataclass(frozen=True)
class CommitRecord:
    audit: AuditRecord
    base: BaseRef
    committed_at: str
    effects: tuple[EffectRef, ...]
    fencing_epoch: int
    generation: RecordRef
    intent_digest: str
    operation_id: str
    session_id: str
    state: RecordRef
    target_generation: int
    transaction_id: str
    schema: str = "mission-commit/1"


@dataclass(frozen=True)
class RecoveryReport:
    session_id: str
    ready: bool
    generation: int
    head_digest: Optional[str]
    commit_digest: Optional[str]


@dataclass(frozen=True)
class ProjectionFileRef:
    digest: str
    identity: tuple[int, int, int, int, int]
    name: str
    size: int


@dataclass(frozen=True)
class ProjectionRecord:
    after: ProjectionFileRef
    base: Optional[ProjectionFileRef]
    blob_id: str
    parent_identity: tuple[int, int, int]
    relative_path: str


@dataclass(frozen=True)
class PrepareRecord:
    audit: AuditRecord
    base: BaseRef
    effects: tuple[EffectRef, ...]
    fencing_epoch: int
    generation: RecordRef
    intent_digest: str
    operation_id: str
    prepared_at: str
    projections: tuple[ProjectionRecord, ...]
    session_id: str
    state: RecordRef
    target_generation: int
    transaction_id: str
    schema: str = "mission-prepare/1"


@dataclass(frozen=True)
class _RecoveryResolution:
    base_generation: int
    disposition: str
    head_digest: Optional[str]
    intent_digest: str
    operation_id: str
    session_id: str
    target_generation: int
    transaction_id: str


@dataclass(frozen=True)
class PendingLease:
    action: str
    base: Union[LegacyAbsentLease, FencedLease]
    target: FencedLease
    admitted_at: str
    base_was_live: bool
    digest: str


@dataclass(frozen=True)
class CommitPrecondition:
    base_generation: int
    base_head_digest: Optional[str]
    pending_lease_digest: str


@dataclass(frozen=True)
class RepositorySnapshot:
    head: HeadRecord
    commit: CommitRecord
    state: MissionState
    state_bytes: bytes
    head_bytes: bytes
    commit_bytes: bytes
    head_digest: str
    result: CommitResult


@dataclass(frozen=True)
class AdmittedSnapshot:
    request: ExecutionRequest
    base: Optional[RepositorySnapshot]
    pending_lease: PendingLease
    target_generation: int
    precondition: CommitPrecondition


@dataclass(frozen=True)
class PreparedCommit:
    admitted: AdmittedSnapshot
    staged: StagedGeneration
    target_state: MissionState
    state_bytes: bytes
    effects: tuple[BlobBinding, ...]
    projections: tuple[ProjectionRecord, ...]
    transaction_id: str
    precondition: CommitPrecondition
    binding_digest: str


@dataclass(frozen=True)
class _PinnedDirectory:
    descriptors: tuple[int, ...]
    names: tuple[str, ...]
    identities: tuple[tuple[int, int, int], ...]

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]


@dataclass(frozen=True)
class _PinnedProjectionTarget:
    descriptors: tuple[int, ...]
    identities: tuple[tuple[int, int, int], ...]
    names: tuple[str, ...]
    target_name: str

    @property
    def descriptor(self) -> int:
        return self.descriptors[-1]

    @property
    def parent_identity(self) -> tuple[int, int, int]:
        return self.identities[-1]


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def compute_intent_digest(
    *,
    session_id: str,
    lease_owner_session_id: str,
    operation_id: str,
    command: FrozenJsonObject,
    blobs: VerifiedBlobSetView,
) -> str:
    """Return the canonical digest of command meaning and captured evidence."""
    session_id = _session_id(session_id)
    lease_owner_session_id = _session_id(lease_owner_session_id)
    operation_id = _token(operation_id, "operation_id")
    if not isinstance(command, FrozenJsonObject):
        raise FencedCommitError("request-invalid", "command must be a FrozenJsonObject")
    try:
        command_bytes = encode_json_object(command)
        normalized_command = decode_json_object(command_bytes, limit=STATE_LIMIT)
    except Exception as exc:
        raise FencedCommitError(
            "request-invalid", "command is not deep-frozen strict JSON"
        ) from exc
    if normalized_command != command:
        raise FencedCommitError("request-invalid", "command is mutable or has duplicate keys")
    try:
        validate_verified_blob_set(blobs)
    except LocalUnitOfWorkError as exc:
        raise FencedCommitError(
            "request-invalid", "blobs are not immutable and verified"
        ) from exc
    bindings = sorted(
        (
            {
                "blob_id": blob.binding.blob_id,
                "digest": blob.binding.digest,
                "kind": blob.binding.kind,
                "relative_path": blob.binding.relative_path,
                "size": blob.binding.size,
            }
            for blob in blobs.blobs
        ),
        key=lambda value: value["blob_id"],
    )
    return _sha256(
        _canonical_bytes(
            {
                "blobs": bindings,
                "command": thaw_json_object(command),
                "lease_owner_session_id": lease_owner_session_id,
                "operation_id": operation_id,
                "schema": "mission-intent/1",
                "session_id": session_id,
            },
            limit=STATE_LIMIT,
        )
    )


def _canonical_bytes(value: dict, *, limit: int, code: str = "record-too-large") -> bytes:
    try:
        content = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FencedCommitError("record-invalid", "record cannot be canonically encoded") from exc
    if len(content) > limit:
        raise FencedCommitError(code, "record exceeds its byte limit")
    return content


def _decode_record(content: bytes, *, limit: int) -> dict:
    try:
        frozen = decode_json_object(content, limit=limit)
        if encode_json_object(frozen) != content:
            raise FencedCommitError("record-not-canonical", "record bytes are not canonical JSON")
        return thaw_json_object(frozen)
    except FencedCommitError:
        raise
    except Exception as exc:
        error_code = getattr(exc, "code", "record-invalid")
        raise FencedCommitError(error_code, "record is not strict canonical JSON") from exc


def _exact(document: dict, keys: set[str], name: str) -> None:
    if not isinstance(document, dict) or set(document) != keys:
        raise FencedCommitError("record-invalid", name + " has an invalid key set")


def _token(value: object, name: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise FencedCommitError("record-invalid", name + " is not a Token128")
    return value


def _session_id(value: object) -> str:
    if not isinstance(value, str) or _SESSION_RE.fullmatch(value) is None:
        raise FencedCommitError("record-invalid", "session_id is invalid")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise FencedCommitError("record-invalid", name + " is not a SHA-256 digest")
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise FencedCommitError("record-invalid", name + " is not a bounded integer")
    return value


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise FencedCommitError("record-invalid", name + " is not canonical UTC seconds")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise FencedCommitError("record-invalid", name + " is not a real timestamp") from exc
    return value


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FencedCommitError("request-invalid", "clock must return an aware datetime")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _format_time(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError) as exc:
        raise FencedCommitError("record-invalid", "lease timestamp is invalid") from exc


def _safe_relative_path(value: object, expected: str, name: str) -> str:
    if not isinstance(value, str):
        raise FencedCommitError("record-invalid", name + " path is not text")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or value != expected
    ):
        raise FencedCommitError("lineage-mismatch", name + " path is not digest-derived")
    return value


def _audit_record(audit: AuditMetadata) -> AuditRecord:
    if not isinstance(audit, AuditMetadata) or type(audit.event_types) is not tuple:
        raise FencedCommitError("audit-metadata-invalid", "audit metadata must be immutable")
    command_type = _token(audit.command_type, "audit.command_type")
    if len(audit.event_types) > MAX_AUDIT_EVENT_TYPES:
        raise FencedCommitError("audit-metadata-invalid", "too many audit event types")
    event_types = tuple(_token(value, "audit.event_types") for value in audit.event_types)
    if len(event_types) != len(set(event_types)):
        raise FencedCommitError("audit-metadata-invalid", "audit event types are duplicated")
    record = AuditRecord(
        command_type_digest=_sha256(command_type.encode("utf-8")),
        event_type_digests=tuple(_sha256(value.encode("utf-8")) for value in event_types),
    )
    _canonical_bytes(_audit_document(record), limit=MAX_AUDIT_BYTES)
    return record


def _audit_document(audit: AuditRecord) -> dict:
    if not isinstance(audit, AuditRecord) or type(audit.event_type_digests) is not tuple:
        raise FencedCommitError("audit-metadata-invalid", "audit record must be immutable")
    command_type_digest = _digest(audit.command_type_digest, "audit.command_type_digest")
    if len(audit.event_type_digests) > MAX_AUDIT_EVENT_TYPES:
        raise FencedCommitError("audit-metadata-invalid", "too many audit event digests")
    event_type_digests = tuple(
        _digest(value, "audit.event_type_digests") for value in audit.event_type_digests
    )
    if len(event_type_digests) != len(set(event_type_digests)):
        raise FencedCommitError("audit-metadata-invalid", "audit event digests are duplicated")
    document = {
        "command_type_digest": command_type_digest,
        "event_type_digests": list(event_type_digests),
    }
    _canonical_bytes(document, limit=MAX_AUDIT_BYTES)
    return document


def _parse_audit(value: object) -> AuditRecord:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", "audit is not an object")
    _exact(value, {"command_type_digest", "event_type_digests"}, "audit")
    event_type_digests = value["event_type_digests"]
    if not isinstance(event_type_digests, list):
        raise FencedCommitError("record-invalid", "audit event_type_digests is not an array")
    audit = AuditRecord(value["command_type_digest"], tuple(event_type_digests))
    _audit_document(audit)
    return audit


def _ref_document(reference: RecordRef) -> dict:
    return {"digest": reference.digest, "path": reference.path, "size": reference.size}


def _parse_ref(value: object, *, directory: str, suffix: str, name: str) -> RecordRef:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", name + " is not an object")
    _exact(value, {"digest", "path", "size"}, name)
    digest = _digest(value["digest"], name + ".digest")
    expected = directory + "/" + digest.removeprefix("sha256:") + suffix
    path = _safe_relative_path(value["path"], expected, name)
    size = _integer(value["size"], name + ".size")
    return RecordRef(digest, path, size)


def _base_document(base: BaseRef) -> dict:
    return {"generation": base.generation, "head_digest": base.head_digest}


def _parse_base(value: object) -> BaseRef:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", "base is not an object")
    _exact(value, {"generation", "head_digest"}, "base")
    generation = _integer(value["generation"], "base.generation")
    head_digest = value["head_digest"]
    if generation == 0:
        if head_digest is not None:
            raise FencedCommitError("record-invalid", "genesis base head digest must be null")
    else:
        head_digest = _digest(head_digest, "base.head_digest")
    return BaseRef(generation, head_digest)


def _effect_document(effect: EffectRef) -> dict:
    return {
        "blob_id": effect.blob_id,
        "digest": effect.digest,
        "kind": effect.kind,
        "object": effect.object,
        "relative_path": effect.relative_path,
        "size": effect.size,
    }


def _parse_effect(value: object) -> EffectRef:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", "effect is not an object")
    _exact(value, {"blob_id", "digest", "kind", "object", "relative_path", "size"}, "effect")
    blob_id = _token(value["blob_id"], "effect.blob_id")
    kind = _token(value["kind"], "effect.kind")
    digest = _digest(value["digest"], "effect.digest")
    object_path = _safe_relative_path(
        value["object"],
        "objects/" + digest.removeprefix("sha256:") + ".blob",
        "effect.object",
    )
    relative_path = value["relative_path"]
    if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 4096:
        raise FencedCommitError("record-invalid", "effect relative_path is invalid")
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or candidate.as_posix() != relative_path or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise FencedCommitError("record-invalid", "effect relative_path is unsafe")
    size = _integer(value["size"], "effect.size")
    if size > STATE_LIMIT:
        raise FencedCommitError("record-invalid", "effect size exceeds state limit")
    return EffectRef(blob_id, digest, kind, object_path, relative_path, size)


def _validate_effect_aggregate(effects: tuple[EffectRef, ...]) -> None:
    if sum(effect.size for effect in effects) > MAX_TOTAL_BLOB_BYTES:
        raise FencedCommitError(
            "blob-set-too-large", "effect bytes exceed the aggregate limit"
        )


def _head_document(head: HeadRecord) -> dict:
    return {
        "commit": _ref_document(head.commit),
        "generation": head.generation,
        "schema": head.schema,
        "session_id": head.session_id,
        "state_generation": _ref_document(head.state_generation),
    }


def parse_head(content: bytes, expected_session: str) -> HeadRecord:
    """Parse a canonical head and bind it to the selected session."""
    document = _decode_record(content, limit=MAX_HEAD_BYTES)
    _exact(document, {"commit", "generation", "schema", "session_id", "state_generation"}, "head")
    if document["schema"] != "mission-head/1":
        raise FencedCommitError("record-invalid", "head schema is invalid")
    session_id = _session_id(document["session_id"])
    if session_id != expected_session:
        raise FencedCommitError("lineage-mismatch", "head session differs from filename")
    return HeadRecord(
        commit=_parse_ref(document["commit"], directory="commits", suffix=".json", name="head.commit"),
        generation=_integer(document["generation"], "head.generation", positive=True),
        session_id=session_id,
        state_generation=_parse_ref(
            document["state_generation"],
            directory="generations",
            suffix=".json",
            name="head.state_generation",
        ),
    )


def _commit_document(commit: CommitRecord) -> dict:
    return {
        "audit": _audit_document(commit.audit),
        "base": _base_document(commit.base),
        "committed_at": commit.committed_at,
        "effects": [_effect_document(effect) for effect in commit.effects],
        "fencing_epoch": commit.fencing_epoch,
        "generation": _ref_document(commit.generation),
        "intent_digest": commit.intent_digest,
        "operation_id": commit.operation_id,
        "schema": commit.schema,
        "session_id": commit.session_id,
        "state": _ref_document(commit.state),
        "target_generation": commit.target_generation,
        "transaction_id": commit.transaction_id,
    }


def _parse_commit(content: bytes) -> CommitRecord:
    document = _decode_record(content, limit=MAX_COMMIT_BYTES)
    keys = {
        "audit", "base", "committed_at", "effects", "fencing_epoch", "generation",
        "intent_digest", "operation_id", "schema", "session_id", "state",
        "target_generation", "transaction_id",
    }
    _exact(document, keys, "commit")
    if document["schema"] != "mission-commit/1":
        raise FencedCommitError("record-invalid", "commit schema is invalid")
    effects_value = document["effects"]
    if not isinstance(effects_value, list) or len(effects_value) > MAX_BLOB_COUNT:
        raise FencedCommitError("record-invalid", "commit effects are invalid")
    effects = tuple(_parse_effect(value) for value in effects_value)
    if len({effect.blob_id for effect in effects}) != len(effects):
        raise FencedCommitError("record-invalid", "commit effect blob IDs are duplicated")
    _validate_effect_aggregate(effects)
    base = _parse_base(document["base"])
    target = _integer(document["target_generation"], "commit.target_generation", positive=True)
    if target != base.generation + 1:
        raise FencedCommitError("record-invalid", "commit generation is not N+1")
    transaction_id = document["transaction_id"]
    if not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise FencedCommitError("record-invalid", "transaction ID is invalid")
    return CommitRecord(
        audit=_parse_audit(document["audit"]),
        base=base,
        committed_at=_timestamp(document["committed_at"], "commit.committed_at"),
        effects=effects,
        fencing_epoch=_integer(document["fencing_epoch"], "commit.fencing_epoch", positive=True),
        generation=_parse_ref(document["generation"], directory="generations", suffix=".json", name="commit.generation"),
        intent_digest=_digest(document["intent_digest"], "commit.intent_digest"),
        operation_id=_token(document["operation_id"], "commit.operation_id"),
        session_id=_session_id(document["session_id"]),
        state=_parse_ref(document["state"], directory="objects", suffix=".blob", name="commit.state"),
        target_generation=target,
        transaction_id=transaction_id,
    )


def _parse_projection_file(
    value: object,
    *,
    expected_name: str,
    name: str,
) -> ProjectionFileRef:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", name + " is not an object")
    _exact(value, {"digest", "identity", "name", "size"}, name)
    identity_value = value["identity"]
    if (
        not isinstance(identity_value, list)
        or len(identity_value) != 5
        or any(type(item) is not int or item < 0 for item in identity_value)
    ):
        raise FencedCommitError("record-invalid", name + " identity is invalid")
    file_name = value["name"]
    if file_name != expected_name:
        raise FencedCommitError("record-invalid", name + " filename differs")
    return ProjectionFileRef(
        digest=_digest(value["digest"], name + ".digest"),
        identity=tuple(identity_value),
        name=file_name,
        size=_integer(value["size"], name + ".size"),
    )


def _parse_projection(value: object, index: int) -> ProjectionRecord:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", "projection is not an object")
    _exact(
        value,
        {"after", "base", "blob_id", "parent_identity", "relative_path"},
        "projection",
    )
    relative_path = value["relative_path"]
    candidate = PurePosixPath(relative_path) if isinstance(relative_path, str) else None
    if (
        candidate is None
        or not relative_path
        or len(relative_path) > 4096
        or candidate.is_absolute()
        or candidate.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise FencedCommitError("record-invalid", "projection path is invalid")
    base_value = value["base"]
    base = None
    if base_value is not None:
        base = _parse_projection_file(
            base_value,
            expected_name="base-%03d.blob" % index,
            name="projection.base",
        )
    parent_identity_value = value["parent_identity"]
    if (
        not isinstance(parent_identity_value, list)
        or len(parent_identity_value) != 3
        or any(type(item) is not int or item < 0 for item in parent_identity_value)
    ):
        raise FencedCommitError(
            "record-invalid", "projection parent identity is invalid"
        )
    return ProjectionRecord(
        after=_parse_projection_file(
            value["after"],
            expected_name="after-%03d.blob" % index,
            name="projection.after",
        ),
        base=base,
        blob_id=_token(value["blob_id"], "projection.blob_id"),
        parent_identity=tuple(parent_identity_value),
        relative_path=relative_path,
    )


def _parse_prepare(content: bytes, filename: str) -> PrepareRecord:
    document = _decode_record(content, limit=MAX_PREPARE_BYTES)
    keys = {
        "audit", "base", "effects", "fencing_epoch", "generation",
        "intent_digest", "operation_id", "prepared_at", "projections",
        "schema", "session_id", "state", "target_generation", "transaction_id",
    }
    _exact(document, keys, "prepare")
    if document["schema"] != "mission-prepare/1":
        raise FencedCommitError("record-invalid", "prepare schema is invalid")
    audit = _parse_audit(document["audit"])
    base = _parse_base(document["base"])
    effects_value = document["effects"]
    if not isinstance(effects_value, list) or len(effects_value) > MAX_BLOB_COUNT:
        raise FencedCommitError("record-invalid", "prepare effects are invalid")
    effects = tuple(_parse_effect(value) for value in effects_value)
    if len({effect.blob_id for effect in effects}) != len(effects):
        raise FencedCommitError("record-invalid", "prepare effect blob IDs are duplicated")
    _validate_effect_aggregate(effects)
    fencing_epoch = _integer(document["fencing_epoch"], "prepare.fencing_epoch", positive=True)
    generation = _parse_ref(
        document["generation"],
        directory="generations",
        suffix=".json",
        name="prepare.generation",
    )
    intent_digest = _digest(document["intent_digest"], "prepare.intent_digest")
    operation_id = _token(document["operation_id"], "prepare.operation_id")
    prepared_at = _timestamp(document["prepared_at"], "prepare.prepared_at")
    projections_value = document["projections"]
    if (
        not isinstance(projections_value, list)
        or len(projections_value) > MAX_BLOB_COUNT
    ):
        raise FencedCommitError("record-invalid", "prepare projections are invalid")
    projections = tuple(
        _parse_projection(value, index)
        for index, value in enumerate(projections_value)
    )
    session_id = _session_id(document["session_id"])
    state = _parse_ref(
        document["state"],
        directory="objects",
        suffix=".blob",
        name="prepare.state",
    )
    target_generation = _integer(
        document["target_generation"],
        "prepare.target_generation",
        positive=True,
    )
    if target_generation != base.generation + 1:
        raise FencedCommitError("record-invalid", "prepare generation is not N+1")
    transaction_id = document["transaction_id"]
    if not isinstance(transaction_id, str) or _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise FencedCommitError("record-invalid", "prepare transaction ID is invalid")
    if filename != transaction_id + ".json":
        raise FencedCommitError("record-invalid", "prepare filename differs from transaction ID")
    if len(projections) != len(effects):
        raise FencedCommitError(
            "record-invalid", "prepare projections and effects differ"
        )
    for projection, effect in zip(projections, effects):
        if (
            projection.blob_id != effect.blob_id
            or projection.relative_path != effect.relative_path
            or projection.after.digest != effect.digest
            or projection.after.size != effect.size
        ):
            raise FencedCommitError(
                "record-invalid", "prepare projection differs from its effect"
            )
    return PrepareRecord(
        audit=audit,
        base=base,
        effects=effects,
        fencing_epoch=fencing_epoch,
        generation=generation,
        intent_digest=intent_digest,
        operation_id=operation_id,
        prepared_at=prepared_at,
        projections=projections,
        session_id=session_id,
        state=state,
        target_generation=target_generation,
        transaction_id=transaction_id,
    )


def _parse_recovery_resolution(
    content: bytes,
    *,
    transaction_filename: Optional[str] = None,
) -> _RecoveryResolution:
    document = _decode_record(content, limit=MAX_OPERATION_BYTES)
    _exact(
        document,
        {
            "base_generation",
            "disposition",
            "head_digest",
            "intent_digest",
            "operation_id",
            "schema",
            "session_id",
            "target_generation",
            "transaction_id",
        },
        "recovery resolution",
    )
    if document["schema"] != "mission-recovery/1":
        raise FencedCommitError(
            "record-invalid", "recovery resolution schema differs"
        )
    transaction_id = document["transaction_id"]
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_RE.fullmatch(transaction_id) is None
    ):
        raise FencedCommitError(
            "record-invalid", "recovery transaction ID is invalid"
        )
    if (
        transaction_filename is not None
        and transaction_filename != transaction_id + ".json"
    ):
        raise FencedCommitError(
            "record-invalid", "recovery resolution filename differs"
        )
    base_generation = _integer(
        document["base_generation"],
        "recovery.base_generation",
    )
    target_generation = _integer(
        document["target_generation"],
        "recovery.target_generation",
        positive=True,
    )
    if target_generation != base_generation + 1:
        raise FencedCommitError(
            "record-invalid", "recovery generation is not N+1"
        )
    disposition = document["disposition"]
    if disposition not in {"rolled-back", "finalized"}:
        raise FencedCommitError(
            "record-invalid", "recovery disposition differs"
        )
    head_digest = document["head_digest"]
    if head_digest is not None:
        head_digest = _digest(head_digest, "recovery.head_digest")
    return _RecoveryResolution(
        base_generation=base_generation,
        disposition=disposition,
        head_digest=head_digest,
        intent_digest=_digest(
            document["intent_digest"], "recovery.intent_digest"
        ),
        operation_id=_token(
            document["operation_id"], "recovery.operation_id"
        ),
        session_id=_session_id(document["session_id"]),
        target_generation=target_generation,
        transaction_id=transaction_id,
    )


def _result_document(result: CommitResult) -> dict:
    return {
        "commit_digest": result.commit_digest,
        "generation": result.generation,
        "head_digest": result.head_digest,
        "state_generation_digest": result.state_generation_digest,
    }


def _parse_result(value: object) -> CommitResult:
    if not isinstance(value, dict):
        raise FencedCommitError("record-invalid", "operation result is not an object")
    _exact(value, {"commit_digest", "generation", "head_digest", "state_generation_digest"}, "result")
    return CommitResult(
        commit_digest=_digest(value["commit_digest"], "result.commit_digest"),
        generation=_integer(value["generation"], "result.generation", positive=True),
        head_digest=_digest(value["head_digest"], "result.head_digest"),
        state_generation_digest=_digest(value["state_generation_digest"], "result.state_generation_digest"),
    )


def _lease_document(lease: Union[LegacyAbsentLease, FencedLease]) -> dict:
    if isinstance(lease, LegacyAbsentLease):
        return {"kind": "legacy-absent"}
    return {
        "fencing_epoch": lease.fencing_epoch,
        "kind": "fenced",
        "lease_expires_at": lease.lease_expires_at,
        "lease_history": [
            {
                "at": entry.at,
                "fencing_epoch": entry.fencing_epoch,
                "lease_id": entry.lease_id,
                "owner_session_id": entry.owner_session_id,
                "reason": entry.reason,
            }
            for entry in lease.lease_history
        ],
        "lease_id": lease.lease_id,
        "owner_session_id": lease.owner_session_id,
    }


def _pending_digest(action: str, base: Union[LegacyAbsentLease, FencedLease], target: FencedLease, admitted_at: str) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "action": action,
                "admitted_at": admitted_at,
                "base": _lease_document(base),
                "target": _lease_document(target),
            },
            limit=STATE_LIMIT,
        )
    )


def _precondition_document(precondition: CommitPrecondition) -> dict:
    return {
        "base_generation": precondition.base_generation,
        "base_head_digest": precondition.base_head_digest,
        "pending_lease_digest": precondition.pending_lease_digest,
    }


def _binding_document(binding: BlobBinding) -> dict:
    return {
        "blob_id": binding.blob_id,
        "digest": binding.digest,
        "kind": binding.kind,
        "relative_path": binding.relative_path,
        "size": binding.size,
    }


def _projection_file_document(reference: ProjectionFileRef) -> dict:
    return {
        "digest": reference.digest,
        "identity": list(reference.identity),
        "name": reference.name,
        "size": reference.size,
    }


def _projection_document(projection: ProjectionRecord) -> dict:
    return {
        "after": _projection_file_document(projection.after),
        "base": (
            None
            if projection.base is None
            else _projection_file_document(projection.base)
        ),
        "blob_id": projection.blob_id,
        "parent_identity": list(projection.parent_identity),
        "relative_path": projection.relative_path,
    }


def _prepared_binding_digest(prepared: PreparedCommit) -> str:
    request = prepared.admitted.request
    pending = prepared.admitted.pending_lease
    base = prepared.admitted.base
    base_identity = None
    if base is not None:
        base_identity = {
            "commit_digest": base.result.commit_digest,
            "generation": base.result.generation,
            "head_digest": base.head_digest,
            "state_generation_digest": base.result.state_generation_digest,
        }
    audit_digest = _sha256(
        _canonical_bytes(
            _audit_document(_audit_record(request.audit)),
            limit=MAX_AUDIT_BYTES,
        )
    )
    presented_token_digest = None
    if request.presented_lease_id is not None:
        presented_token_digest = _sha256(
            request.presented_lease_id.encode("utf-8")
        )
    document = {
        "admission": {
            "base_identity": base_identity,
            "pending_lease": {
                "action": pending.action,
                "admitted_at": pending.admitted_at,
                "base": _lease_document(pending.base),
                "base_was_live": pending.base_was_live,
                "digest": pending.digest,
                "target": _lease_document(pending.target),
            },
            "precondition": _precondition_document(prepared.admitted.precondition),
            "target_generation": prepared.admitted.target_generation,
        },
        "prepared": {
            "effects": [_binding_document(effect) for effect in prepared.effects],
            "projections": [
                _projection_document(projection)
                for projection in prepared.projections
            ],
            "precondition": _precondition_document(prepared.precondition),
            "state_digest": _sha256(prepared.state_bytes),
            "state_size": len(prepared.state_bytes),
            "transaction_id": prepared.transaction_id,
        },
        "request": {
            "audit_digest": audit_digest,
            "blobs": [
                _binding_document(blob.binding) for blob in request.blobs.blobs
            ],
            "command": thaw_json_object(request.command),
            "intent_digest": request.intent_digest,
            "lease_owner_session_id": request.lease_owner_session_id,
            "operation_id": request.operation_id,
            "presented_lease_token_digest": presented_token_digest,
            "session_id": request.session_id,
        },
        "schema": "mission-prepared-binding/1",
        "stage": {
            "generation_digest": prepared.staged.generation_digest,
            "manifest_bytes_digest": _sha256(prepared.staged.manifest_bytes),
            "manifest_bytes_size": len(prepared.staged.manifest_bytes),
            "manifest_identity": list(prepared.staged.manifest_identity),
            "objects_identity": list(prepared.staged.objects_identity),
            "root_identity": list(prepared.staged.root_identity),
            "root_name": prepared.staged.root.name,
            "state_identity": list(prepared.staged.state_identity),
        },
    }
    return _sha256(_canonical_bytes(document, limit=STATE_LIMIT))


def validate_execution_request(request: ExecutionRequest) -> None:
    """Validate the shared immutable request before either writer uses it."""
    if not isinstance(request, ExecutionRequest):
        raise FencedCommitError("request-invalid", "request type is invalid")
    _session_id(request.session_id)
    _session_id(request.lease_owner_session_id)
    _token(request.operation_id, "operation_id")
    _digest(request.intent_digest, "intent_digest")
    if not isinstance(request.command, FrozenJsonObject):
        raise FencedCommitError("request-invalid", "command must be a FrozenJsonObject")
    if request.typed_command is not None:
        try:
            encoded_command = encode_kernel_command(request.typed_command)
            expected_command_type = kernel_command_type(request.typed_command)
        except (TypeError, ValueError) as exc:
            raise FencedCommitError("request-invalid", "typed command is invalid") from exc
        if encoded_command != request.command:
            raise FencedCommitError(
                "command-binding-mismatch", "typed and immutable commands differ"
            )
        if request.audit.command_type != expected_command_type:
            raise FencedCommitError(
                "audit-binding-mismatch", "audit command category differs"
            )
    expected_intent = compute_intent_digest(
        session_id=request.session_id,
        lease_owner_session_id=request.lease_owner_session_id,
        operation_id=request.operation_id,
        command=request.command,
        blobs=request.blobs,
    )
    if expected_intent != request.intent_digest:
        raise FencedCommitError(
            "intent-digest-mismatch", "request and versioned intent digest differ"
        )
    if request.presented_lease_id is not None:
        _token(request.presented_lease_id, "presented_lease_id")
    _audit_record(request.audit)


def admit_lease(
    request: ExecutionRequest,
    base: Union[LegacyAbsentLease, FencedLease],
    admitted_at_value: datetime,
    lease_ttl_seconds: int,
    generated_lease_id: Optional[str] = None,
) -> PendingLease:
    """Derive the pending fenced lease shared by v4 and v5 repositories."""
    admitted_at = _as_utc(admitted_at_value)
    admitted_text = _format_time(admitted_at)
    expiry = _format_time(admitted_at + timedelta(seconds=lease_ttl_seconds))
    if generated_lease_id is not None:
        _token(generated_lease_id, "generated_lease_id")
        if request.presented_lease_id is not None:
            raise FencedCommitError(
                "request-invalid", "generated token cannot replace a presented token"
            )
    if isinstance(base, LegacyAbsentLease):
        lease_id = request.presented_lease_id or generated_lease_id or secrets.token_hex(16)
        target = FencedLease(request.lease_owner_session_id, lease_id, 1, expiry, ())
        action = "acquired"
        base_was_live = False
    else:
        base_expiry = _parse_time(base.lease_expires_at)
        base_was_live = admitted_at < base_expiry
        if (
            base.owner_session_id == request.lease_owner_session_id
            and request.presented_lease_id == base.lease_id
        ):
            renewed_expiry = max(base_expiry, admitted_at + timedelta(seconds=lease_ttl_seconds))
            target = FencedLease(
                base.owner_session_id,
                base.lease_id,
                base.fencing_epoch,
                _format_time(renewed_expiry),
                base.lease_history,
            )
            action = "renewed"
        elif base_was_live:
            if (
                base.owner_session_id == request.lease_owner_session_id
                and request.presented_lease_id is None
            ):
                raise FencedCommitError("lease-token-required", "the matching owner omitted its token")
            raise FencedCommitError("lease-rejected", "the current fenced lease is still live")
        else:
            retired = {entry.lease_id for entry in base.lease_history}
            if request.presented_lease_id == base.lease_id or (
                request.presented_lease_id is not None
                and request.presented_lease_id in retired
            ):
                raise FencedCommitError("stale-fencing-token", "the presented token is retired")
            new_lease_id = (
                request.presented_lease_id or generated_lease_id or secrets.token_hex(16)
            )
            history = base.lease_history + (
                LeaseHistoryEntry(
                    owner_session_id=base.owner_session_id,
                    lease_id=base.lease_id,
                    fencing_epoch=base.fencing_epoch,
                    reason="lease-expired-takeover",
                    at=admitted_text,
                ),
            )
            target = FencedLease(
                request.lease_owner_session_id,
                new_lease_id,
                base.fencing_epoch + 1,
                expiry,
                history,
            )
            action = "taken-over"
    return PendingLease(
        action=action,
        base=base,
        target=target,
        admitted_at=admitted_text,
        base_was_live=base_was_live,
        digest=_pending_digest(action, base, target, admitted_text),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


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


class LocalFencedRepository:
    def __init__(
        self,
        repository_root: Path | str,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        fault_injector: Optional[Callable[[str], None]] = None,
        lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ):
        if type(lease_ttl_seconds) is not int or lease_ttl_seconds <= 0:
            raise FencedCommitError("request-invalid", "lease TTL must be a positive integer")
        self.root = Path(repository_root)
        self.clock = clock or _utc_now
        self.fault_injector = fault_injector
        self.lease_ttl_seconds = lease_ttl_seconds
        self._root_descriptor: Optional[int] = None
        self._root_identity: Optional[tuple[int, int, int]] = None
        self._lock_descriptor: Optional[int] = None
        self._lock_identity: Optional[tuple[int, int, int, int, int, int, int]] = None
        self._stage_binding_registry: dict[str, str] = {}

    def _fault(self, point: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(point)

    def _ensure_directory(self, path: Path) -> None:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise FencedCommitError(
                    "repository-invalid", "required repository path is not a directory"
                )
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                os.chmod(path, 0o700, follow_symlinks=False)
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "repository-invalid", "repository directory cannot be created safely"
            ) from exc

    def _ensure_layout(self) -> None:
        self._ensure_directory(self.root)
        root_dev = self.root.lstat().st_dev
        for path in (
            self.root / "sessions",
            self.root / "transactions",
            self.root / "transactions" / "prepared",
            self.root / "transactions" / "projections",
            self.root / "transactions" / "quarantine",
            self.root / "transactions" / "resolved",
            self.root / "transactions" / "resolved-operations",
            self.root / "transactions" / "resolved-operations" / "finalized",
            self.root / "transactions" / "resolved-operations" / "rolled-back",
            self.root / "objects",
            self.root / "generations",
            self.root / "commits",
            self.root / "operations",
        ):
            self._ensure_directory(path)
            if path.lstat().st_dev != root_dev:
                raise FencedCommitError("repository-invalid", "repository layout crosses filesystems")

    def _verify_root(self) -> None:
        if self._root_descriptor is None or self._root_identity is None:
            raise FencedCommitError("repository-invalid", "repository root is not pinned")
        try:
            if (
                _directory_identity(os.fstat(self._root_descriptor)) != self._root_identity
                or _directory_identity(self.root.lstat()) != self._root_identity
            ):
                raise FencedCommitError("repository-changed", "repository root identity changed")
            if self._lock_descriptor is not None and self._lock_identity is not None:
                named_lock = os.stat(
                    ".state.lock",
                    dir_fd=self._root_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _file_identity(os.fstat(self._lock_descriptor)) != self._lock_identity
                    or _file_identity(named_lock) != self._lock_identity
                ):
                    raise FencedCommitError("repository-changed", "repository lock identity changed")
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError("repository-changed", "repository root identity changed") from exc

    def _verify_pinned_directory(self, pinned: _PinnedDirectory) -> None:
        self._verify_root()
        for index, name in enumerate(pinned.names):
            parent_descriptor = pinned.descriptors[index]
            child_descriptor = pinned.descriptors[index + 1]
            identity = pinned.identities[index + 1]
            try:
                named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                if (
                    _directory_identity(os.fstat(child_descriptor)) != identity
                    or _directory_identity(named) != identity
                    or not stat.S_ISDIR(named.st_mode)
                ):
                    raise FencedCommitError(
                        "repository-changed", "authoritative directory identity changed"
                    )
            except FencedCommitError:
                raise
            except OSError as exc:
                raise FencedCommitError(
                    "repository-changed", "authoritative directory identity changed"
                ) from exc

    @contextmanager
    def _pinned_directory(self, *parts: str):
        self._verify_root()
        assert self._root_descriptor is not None and self._root_identity is not None
        descriptors = [self._root_descriptor]
        identities = [self._root_identity]
        try:
            for part in parts:
                if not part or "/" in part or part in {".", ".."}:
                    raise FencedCommitError("repository-invalid", "directory component is unsafe")
                parent_descriptor = descriptors[-1]
                descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_descriptor,
                )
                descriptors.append(descriptor)
                opened = os.fstat(descriptor)
                named = os.stat(part, dir_fd=parent_descriptor, follow_symlinks=False)
                identity = _directory_identity(opened)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or _directory_identity(named) != identity
                    or identity[0] != self._root_identity[0]
                ):
                    raise FencedCommitError(
                        "repository-changed", "authoritative directory cannot be pinned"
                    )
                identities.append(identity)
            pinned = _PinnedDirectory(tuple(descriptors), tuple(parts), tuple(identities))
            self._verify_pinned_directory(pinned)
            yield pinned
            self._verify_pinned_directory(pinned)
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "repository-changed", "authoritative directory cannot be pinned"
            ) from exc
        finally:
            for descriptor in reversed(descriptors[1:]):
                os.close(descriptor)

    def _read_pinned_file(
        self,
        pinned: _PinnedDirectory,
        name: str,
        *,
        limit: int,
        allow_missing: bool = False,
    ) -> Optional[bytes]:
        if not name or "/" in name or name in {".", ".."}:
            raise FencedCommitError("record-invalid", "record filename is unsafe")
        self._verify_pinned_directory(pinned)
        try:
            named = os.stat(name, dir_fd=pinned.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if allow_missing:
                self._verify_pinned_directory(pinned)
                return None
            raise FencedCommitError("record-missing", "authoritative record is missing")
        except OSError as exc:
            raise FencedCommitError("record-invalid", "authoritative record cannot be stated") from exc
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or named.st_size > limit
        ):
            raise FencedCommitError("record-invalid", "authoritative record identity is invalid")
        descriptor = None
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=pinned.descriptor,
            )
            opened = os.fstat(descriptor)
            expected = _file_identity(named)
            if _file_identity(opened) != expected:
                raise FencedCommitError("record-invalid", "authoritative record identity changed")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            final_opened = os.fstat(descriptor)
            final_named = os.stat(name, dir_fd=pinned.descriptor, follow_symlinks=False)
            if (
                len(content) > limit
                or _file_identity(final_opened) != expected
                or _file_identity(final_named) != expected
                or len(content) != named.st_size
            ):
                raise FencedCommitError("record-invalid", "authoritative record changed while read")
            self._verify_pinned_directory(pinned)
            return content
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError("record-invalid", "authoritative record cannot be read") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def _lock(self):
        self._ensure_layout()
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise FencedCommitError("repository-invalid", "platform cannot pin repository safely")
        root_descriptor = None
        descriptor = None
        root_locked = False
        try:
            root_descriptor = os.open(
                os.fspath(self.root),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            root_identity = _directory_identity(os.fstat(root_descriptor))
            if (
                not stat.S_ISDIR(root_identity[2])
                or _directory_identity(self.root.lstat()) != root_identity
            ):
                raise FencedCommitError("repository-changed", "repository root identity changed")
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(root_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    root_locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise FencedCommitError("lock-timeout", "repository lock timed out")
                    time.sleep(0.05)
            descriptor = os.open(
                ".state.lock",
                os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_descriptor,
            )
            lock_metadata = os.fstat(descriptor)
            named_lock = os.stat(".state.lock", dir_fd=root_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or lock_metadata.st_nlink != 1
                or _file_identity(lock_metadata) != _file_identity(named_lock)
            ):
                raise FencedCommitError("repository-invalid", "repository lock identity is invalid")
            if stat.S_IMODE(lock_metadata.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise FencedCommitError("lock-timeout", "repository lock timed out")
                    time.sleep(0.05)
            locked = os.fstat(descriptor)
            named_locked = os.stat(".state.lock", dir_fd=root_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(locked.st_mode)
                or locked.st_nlink != 1
                or _file_identity(locked) != _file_identity(named_locked)
            ):
                raise FencedCommitError("repository-changed", "repository lock identity changed")
            self._root_descriptor = root_descriptor
            self._root_identity = root_identity
            self._lock_descriptor = descriptor
            self._lock_identity = _file_identity(locked)
            self._verify_root()
            yield
            self._verify_root()
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError("repository-invalid", "repository lock cannot be opened safely") from exc
        finally:
            self._root_descriptor = None
            self._root_identity = None
            self._lock_descriptor = None
            self._lock_identity = None
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            if root_descriptor is not None:
                if root_locked:
                    fcntl.flock(root_descriptor, fcntl.LOCK_UN)
                os.close(root_descriptor)

    def _read_exact(self, reference: RecordRef, *, limit: int) -> bytes:
        parts = PurePosixPath(reference.path).parts
        if len(parts) != 2:
            raise FencedCommitError("lineage-mismatch", "referenced record path is invalid")
        with self._pinned_directory(parts[0]) as pinned:
            content = self._read_pinned_file(pinned, parts[1], limit=limit)
        assert content is not None
        if len(content) != reference.size or _sha256(content) != reference.digest:
            raise FencedCommitError("lineage-mismatch", "referenced record digest or size differs")
        return content

    def _read_head_unlocked(self, session_id: str) -> tuple[Optional[HeadRecord], Optional[bytes], Optional[str]]:
        with self._pinned_directory("sessions") as pinned:
            content = self._read_pinned_file(
                pinned,
                session_id + ".json",
                limit=MAX_HEAD_BYTES,
                allow_missing=True,
            )
        if content is None:
            return None, None, None
        return parse_head(content, session_id), content, _sha256(content)

    def _gc_commit_fact_unlocked(self, name: str) -> tuple[CommitRecord, str]:
        if not name.endswith(".json") or _DIGEST_RE.fullmatch("sha256:" + name[:-5]) is None:
            raise FencedCommitError("record-invalid", "commit filename is invalid")
        with self._pinned_directory("commits") as pinned:
            content = self._read_pinned_file(pinned, name, limit=MAX_COMMIT_BYTES)
        assert content is not None
        digest = _sha256(content)
        if name != digest.removeprefix("sha256:") + ".json":
            raise FencedCommitError("record-invalid", "commit filename digest differs")
        commit = _parse_commit(content)
        commit_ref = RecordRef(digest, "commits/" + name, len(content))
        prior_head = HeadRecord(
            commit=commit_ref,
            generation=commit.target_generation,
            session_id=commit.session_id,
            state_generation=commit.generation,
        )
        prior_head_digest = _sha256(
            _canonical_bytes(_head_document(prior_head), limit=MAX_HEAD_BYTES)
        )
        return commit, prior_head_digest

    def _manifest_records(self, content: bytes) -> tuple[RecordRef, tuple[EffectRef, ...]]:
        document = _decode_record(content, limit=STATE_LIMIT)
        _exact(document, {"schema", "state", "blobs"}, "generation manifest")
        if document["schema"] != "mission-generation/1":
            raise FencedCommitError("lineage-mismatch", "generation manifest schema differs")
        state_value = document["state"]
        if not isinstance(state_value, dict):
            raise FencedCommitError("record-invalid", "manifest state is not an object")
        _exact(state_value, {"digest", "object", "size"}, "manifest state")
        state_digest = _digest(state_value["digest"], "manifest.state.digest")
        state = RecordRef(
            state_digest,
            _safe_relative_path(
                state_value["object"],
                "objects/" + state_digest.removeprefix("sha256:") + ".blob",
                "manifest.state",
            ),
            _integer(state_value["size"], "manifest.state.size"),
        )
        blobs_value = document["blobs"]
        if not isinstance(blobs_value, list) or len(blobs_value) > MAX_BLOB_COUNT:
            raise FencedCommitError("record-invalid", "manifest blobs are invalid")
        effects = tuple(_parse_effect(value) for value in blobs_value)
        if len({effect.blob_id for effect in effects}) != len(effects):
            raise FencedCommitError(
                "record-invalid", "manifest effect blob IDs are duplicated"
            )
        _validate_effect_aggregate(effects)
        return state, effects

    def _read_snapshot_unlocked(self, session_id: str) -> RepositorySnapshot:
        head, head_bytes, head_digest = self._read_head_unlocked(session_id)
        if head is None or head_bytes is None or head_digest is None:
            raise FencedCommitError("session-not-found", "v5 session head does not exist")
        return self._read_snapshot_from_head_unlocked(
            session_id,
            head,
            head_bytes,
            head_digest,
        )

    def _read_snapshot_from_head_unlocked(
        self,
        session_id: str,
        head: HeadRecord,
        head_bytes: bytes,
        head_digest: str,
    ) -> RepositorySnapshot:
        commit_bytes = self._read_exact(head.commit, limit=MAX_COMMIT_BYTES)
        commit = _parse_commit(commit_bytes)
        if (
            commit.session_id != session_id
            or commit.target_generation != head.generation
            or commit.generation != head.state_generation
            or _sha256(commit_bytes) != head.commit.digest
        ):
            raise FencedCommitError("lineage-mismatch", "head and commit lineage differ")
        manifest_bytes = self._read_exact(head.state_generation, limit=STATE_LIMIT)
        state_ref, effects = self._manifest_records(manifest_bytes)
        if state_ref != commit.state or effects != commit.effects:
            raise FencedCommitError("lineage-mismatch", "commit and generation manifest differ")
        state_bytes = self._read_exact(state_ref, limit=STATE_LIMIT)
        try:
            state = decode_mission_state(state_bytes)
        except Exception as exc:
            raise FencedCommitError(getattr(exc, "code", "record-invalid"), "state generation is invalid") from exc
        if state.identity.session_id is not None and state.identity.session_id != session_id:
            raise FencedCommitError("lineage-mismatch", "state session identity differs")
        if not isinstance(state.lease, FencedLease) or state.lease.fencing_epoch != commit.fencing_epoch:
            raise FencedCommitError("lineage-mismatch", "state lease fence differs from commit")
        for effect in effects:
            self._read_exact(RecordRef(effect.digest, effect.object, effect.size), limit=STATE_LIMIT)
        result = CommitResult(
            commit_digest=head.commit.digest,
            generation=head.generation,
            head_digest=head_digest,
            state_generation_digest=head.state_generation.digest,
        )
        return RepositorySnapshot(
            head=head,
            commit=commit,
            state=state,
            state_bytes=state_bytes,
            head_bytes=head_bytes,
            commit_bytes=commit_bytes,
            head_digest=head_digest,
            result=result,
        )

    def read(self, session_id: str) -> RepositorySnapshot:
        session_id = _session_id(session_id)
        with self._lock():
            return self._read_snapshot_unlocked(session_id)

    def _operation_path(self, session_id: str, operation_id: str) -> Path:
        key = _canonical_bytes(
            {
                "operation_id": _token(operation_id, "operation_id"),
                "schema": "mission-operation-key/1",
                "session_id": _session_id(session_id),
            },
            limit=MAX_OPERATION_BYTES,
        )
        name = hashlib.sha256(key).hexdigest() + ".json"
        return self.root / "operations" / name

    def _lookup_operation(self, request: ExecutionRequest) -> Optional[CommitResult]:
        name = self._operation_path(request.session_id, request.operation_id).name
        with self._pinned_directory("operations") as pinned:
            content = self._read_pinned_file(
                pinned,
                name,
                limit=MAX_OPERATION_BYTES,
                allow_missing=True,
            )
        if content is None:
            return None
        document = _decode_record(content, limit=MAX_OPERATION_BYTES)
        _exact(
            document,
            {"commit_digest", "intent_digest", "operation_id", "result", "schema", "session_id"},
            "operation",
        )
        if document["schema"] != "mission-operation/1":
            raise FencedCommitError("record-invalid", "operation schema is invalid")
        operation_id = _token(document["operation_id"], "operation.operation_id")
        intent_digest = _digest(document["intent_digest"], "operation.intent_digest")
        session_id = _session_id(document["session_id"])
        commit_digest = _digest(document["commit_digest"], "operation.commit_digest")
        result = _parse_result(document["result"])
        if operation_id != request.operation_id or session_id != request.session_id:
            raise FencedCommitError("lineage-mismatch", "operation record identity differs")
        if intent_digest != request.intent_digest:
            raise FencedCommitError("operation-intent-collision", "operation ID has a different intent")
        if commit_digest != result.commit_digest:
            raise FencedCommitError("lineage-mismatch", "operation result commit differs")
        return result

    def _resolved_operation_path(
        self,
        session_id: str,
        operation_id: str,
        disposition: str = "finalized",
    ) -> Path:
        if disposition not in {"rolled-back", "finalized"}:
            raise FencedCommitError(
                "recovery-ambiguous", "recovery index disposition is invalid"
            )
        return (
            self.root
            / "transactions"
            / "resolved-operations"
            / disposition
            / self._operation_path(session_id, operation_id).name
        )

    def _resolution_index_document(
        self,
        resolution: _RecoveryResolution,
    ) -> dict:
        return {
            "disposition": resolution.disposition,
            "intent_digest": resolution.intent_digest,
            "operation_id": resolution.operation_id,
            "schema": "mission-recovery-operation/1",
            "session_id": resolution.session_id,
        }

    def _parse_resolution_index(
        self,
        content: bytes,
        *,
        disposition: str,
        filename: str,
    ) -> tuple[str, str, str]:
        document = _decode_record(content, limit=MAX_OPERATION_BYTES)
        _exact(
            document,
            {
                "disposition",
                "intent_digest",
                "operation_id",
                "schema",
                "session_id",
            },
            "recovery operation index",
        )
        if document["schema"] != "mission-recovery-operation/1":
            raise FencedCommitError(
                "record-invalid", "recovery operation index schema differs"
            )
        if document["disposition"] != disposition:
            raise FencedCommitError(
                "record-invalid", "recovery operation index disposition differs"
            )
        session_id = _session_id(document["session_id"])
        operation_id = _token(
            document["operation_id"], "recovery.operation_id"
        )
        intent_digest = _digest(
            document["intent_digest"], "recovery.intent_digest"
        )
        if (
            self._resolved_operation_path(
                session_id,
                operation_id,
                disposition,
            ).name
            != filename
        ):
            raise FencedCommitError(
                "record-invalid", "recovery operation index filename differs"
            )
        return session_id, operation_id, intent_digest

    def _publish_resolution_index_unlocked(
        self,
        resolution: _RecoveryResolution,
    ) -> None:
        content = _canonical_bytes(
            self._resolution_index_document(resolution),
            limit=MAX_OPERATION_BYTES,
        )
        self._publish_named_immutable(
            self._resolved_operation_path(
                resolution.session_id,
                resolution.operation_id,
                resolution.disposition,
            ),
            content,
            limit=MAX_OPERATION_BYTES,
            collision_code="recovery-ambiguous",
        )

    def _ensure_resolution_index_unlocked(self) -> None:
        ready_content = _canonical_bytes(
            {"schema": "mission-recovery-operation-index/1"},
            limit=MAX_OPERATION_BYTES,
        )
        ready_path = (
            self.root
            / "transactions"
            / "resolved-operations"
            / ".ready-v1.json"
        )
        with self._pinned_directory(
            "transactions", "resolved-operations"
        ) as pinned:
            current = self._read_pinned_file(
                pinned,
                ready_path.name,
                limit=MAX_OPERATION_BYTES,
                allow_missing=True,
            )
        if current is not None:
            if current != ready_content:
                raise FencedCommitError(
                    "recovery-ambiguous", "recovery operation index marker differs"
                )
            return

        for disposition in ("finalized", "rolled-back"):
            with self._pinned_directory(
                "transactions", "resolved-operations", disposition
            ) as pinned:
                try:
                    entries = sorted(os.listdir(pinned.descriptor))
                except OSError as exc:
                    raise FencedCommitError(
                        "recovery-ambiguous",
                        "recovery operation index cannot be inspected",
                    ) from exc
                self._verify_pinned_directory(pinned)
                for name in entries:
                    if (
                        not name.endswith(".json")
                        or _DIGEST_RE.fullmatch("sha256:" + name[:-5]) is None
                    ):
                        raise FencedCommitError(
                            "recovery-ambiguous",
                            "recovery operation index filename is invalid",
                        )
                    content = self._read_pinned_file(
                        pinned,
                        name,
                        limit=MAX_OPERATION_BYTES,
                    )
                    assert content is not None
                    try:
                        self._parse_resolution_index(
                            content,
                            disposition=disposition,
                            filename=name,
                        )
                    except FencedCommitError as exc:
                        raise FencedCommitError(
                            "recovery-ambiguous",
                            "recovery operation index is malformed",
                        ) from exc

        with self._pinned_directory("transactions", "resolved") as pinned:
            try:
                entries = sorted(os.listdir(pinned.descriptor))
            except OSError as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "resolved transaction index cannot be inspected"
                ) from exc
            self._verify_pinned_directory(pinned)
            for name in entries:
                if (
                    not name.endswith(".json")
                    or _TRANSACTION_RE.fullmatch(name[:-5]) is None
                ):
                    raise FencedCommitError(
                        "recovery-ambiguous", "resolved transaction filename is invalid"
                    )
                content = self._read_pinned_file(
                    pinned,
                    name,
                    limit=MAX_OPERATION_BYTES,
                )
                assert content is not None
                try:
                    resolution = _parse_recovery_resolution(
                        content,
                        transaction_filename=name,
                    )
                except FencedCommitError as exc:
                    raise FencedCommitError(
                        "recovery-ambiguous", "resolved transaction record is malformed"
                    ) from exc
                self._publish_resolution_index_unlocked(resolution)

        self._publish_named_immutable(
            ready_path,
            ready_content,
            limit=MAX_OPERATION_BYTES,
            collision_code="recovery-ambiguous",
        )

    def _check_resolved_operation(self, request: ExecutionRequest) -> None:
        self._ensure_resolution_index_unlocked()
        found = {}
        for disposition in ("rolled-back", "finalized"):
            path = self._resolved_operation_path(
                request.session_id,
                request.operation_id,
                disposition,
            )
            with self._pinned_directory(
                "transactions", "resolved-operations", disposition
            ) as pinned:
                content = self._read_pinned_file(
                    pinned,
                    path.name,
                    limit=MAX_OPERATION_BYTES,
                    allow_missing=True,
                )
            if content is None:
                continue
            try:
                session_id, operation_id, intent_digest = self._parse_resolution_index(
                    content,
                    disposition=disposition,
                    filename=path.name,
                )
            except FencedCommitError as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "recovery operation index is malformed"
                ) from exc
            if (
                session_id != request.session_id
                or operation_id != request.operation_id
            ):
                raise FencedCommitError(
                    "recovery-ambiguous", "recovery operation index identity differs"
                )
            found[disposition] = intent_digest
        if any(
            intent_digest != request.intent_digest
            for intent_digest in found.values()
        ):
            raise FencedCommitError(
                "operation-intent-collision",
                "resolved operation ID has a different intent",
            )
        if "finalized" in found:
            raise FencedCommitError(
                "recovery-ambiguous",
                "finalized transaction is missing its operation tombstone",
            )

    def _reject_open_prepare(self) -> None:
        with self._pinned_directory("transactions", "prepared") as pinned:
            try:
                entries = os.listdir(pinned.descriptor)
            except OSError as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "prepare directory cannot be inspected"
                ) from exc
            self._verify_pinned_directory(pinned)
            if entries:
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "the repository has an unexpected durable prepare",
                )

    def begin(self, request: ExecutionRequest) -> Union[AdmittedSnapshot, CommitResult]:
        validate_execution_request(request)
        with self._lock():
            prepared_entries = self._prepared_entries_unlocked()
            if not prepared_entries:
                recorded = self._lookup_operation(request)
                if recorded is not None:
                    return recorded
                self._recover_orphan_stages_unlocked()
            else:
                self._recover_unlocked(request.session_id)
            recorded = self._lookup_operation(request)
            if recorded is not None:
                return recorded
            self._check_resolved_operation(request)
            head, _head_bytes, head_digest = self._read_head_unlocked(request.session_id)
            if head is None:
                base = None
                base_generation = 0
                base_lease: Union[LegacyAbsentLease, FencedLease] = LegacyAbsentLease()
            else:
                assert _head_bytes is not None and head_digest is not None
                base = self._read_snapshot_from_head_unlocked(
                    request.session_id,
                    head,
                    _head_bytes,
                    head_digest,
                )
                base_generation = base.head.generation
                base_lease = base.state.lease
            pending = admit_lease(request, base_lease, self.clock(), self.lease_ttl_seconds)
            precondition = CommitPrecondition(base_generation, head_digest, pending.digest)
            return AdmittedSnapshot(request, base, pending, base_generation + 1, precondition)

    def stage(
        self,
        admitted: AdmittedSnapshot,
        transition: Transition,
        blobs: VerifiedBlobSet,
    ) -> PreparedCommit:
        """Stage only a transition issued by the canonical kernel decision table."""
        if not isinstance(admitted, AdmittedSnapshot):
            raise FencedCommitError("request-invalid", "admitted snapshot type is invalid")
        if not is_sealed_transition(transition):
            raise FencedCommitError("transition-invalid", "transition is not kernel-issued")
        if blobs != admitted.request.blobs:
            raise FencedCommitError("blob-binding-mismatch", "stage blobs differ from request")
        try:
            validate_verified_blob_set(blobs)
        except LocalUnitOfWorkError as exc:
            raise FencedCommitError(exc.code, exc.detail) from exc
        if not isinstance(transition.new_state, MissionState):
            raise FencedCommitError("transition-invalid", "transition state type is invalid")
        target_state = replace(transition.new_state, snapshot_provenance=None)
        if target_state.identity.session_id != admitted.request.session_id:
            raise FencedCommitError("lineage-mismatch", "transition session differs")
        if target_state.lease != admitted.pending_lease.target:
            raise FencedCommitError(
                "pending-lease-mismatch", "transition does not contain the pending lease"
            )
        if admitted.base is None or admitted.request.typed_command is None:
            raise FencedCommitError(
                "transition-binding-mismatch", "typed transition has no admitted base command"
            )
        admitted_state = replace(
            admitted.base.state,
            lease=admitted.pending_lease.target,
            snapshot_provenance=None,
        )
        if not is_transition_bound_to(
            transition,
            admitted_state,
            admitted.request.typed_command,
        ):
            raise FencedCommitError(
                "transition-binding-mismatch",
                "transition differs from its admitted state or command",
            )
        canonical_decision = decide(admitted_state, admitted.request.typed_command)
        canonical_transition = canonical_decision.transition
        if (
            not canonical_decision.accepted
            or canonical_transition is None
            or transition.new_state != canonical_transition.new_state
            or transition.events != canonical_transition.events
        ):
            raise FencedCommitError(
                "transition-binding-mismatch",
                "transition differs from canonical decision output",
            )
        event_types = tuple(event.type for event in transition.events)
        if event_types != admitted.request.audit.event_types:
            raise FencedCommitError(
                "audit-binding-mismatch", "audit event categories differ"
            )
        effects = transition.effects
        if type(effects) is not tuple or any(
            not isinstance(effect, BlobBinding) for effect in effects
        ):
            raise FencedCommitError("effect-binding-invalid", "transition effects are invalid")
        expected_effects = tuple(blob.binding for blob in blobs.blobs)
        if effects != expected_effects:
            raise FencedCommitError(
                "blob-binding-mismatch", "transition effects differ from captured blobs"
            )
        try:
            if target_state.schema_origin is SchemaOrigin.V5:
                if admitted.base is None:
                    raise FencedCommitError(
                        "transition-invalid", "v5 transition requires an admitted base"
                    )
                guidance = decode_snapshot(admitted.base.state_bytes).guidance
                state_bytes = encode_v5_state(target_state, guidance)
            else:
                state_bytes = project_legacy_document(target_state)
        except FencedCommitError:
            raise
        except Exception as exc:
            raise FencedCommitError(
                getattr(exc, "code", "transition-invalid"),
                "transition cannot be encoded canonically",
            ) from exc
        return self._stage_persistence(
            admitted,
            state_bytes=state_bytes,
            effects=effects,
        )

    def execute(
        self,
        request: ExecutionRequest,
    ) -> RepositoryExecutionResult:
        """Run one explicit request through admission, decision, stage, and commit."""
        validate_execution_request(request)
        if request.typed_command is None:
            raise FencedCommitError("request-invalid", "typed command is required")
        admitted = self.begin(request)
        if isinstance(admitted, CommitResult):
            return RepositoryExecutionResult(True, admitted, None)
        if admitted.base is None:
            return RepositoryExecutionResult(False, None, "initial-state-required")
        admitted_state = replace(
            admitted.base.state,
            lease=admitted.pending_lease.target,
            snapshot_provenance=None,
        )
        decision = decide(admitted_state, request.typed_command)
        if not isinstance(decision, Decision):
            raise FencedCommitError("decision-invalid", "decision result type is invalid")
        if not decision.accepted:
            if decision.transition is not None or decision.rejection is None:
                raise FencedCommitError("decision-invalid", "rejected decision is not closed")
            return RepositoryExecutionResult(False, None, decision.rejection.code)
        if decision.transition is None or decision.rejection is not None:
            raise FencedCommitError("decision-invalid", "accepted decision is not closed")
        event_types = tuple(event.type for event in decision.events)
        if event_types != request.audit.event_types:
            raise FencedCommitError(
                "audit-binding-mismatch", "audit event categories differ"
            )
        transition = decision.transition
        if request.blobs.blobs:
            transition = bind_transition_effects(
                transition,
                tuple(blob.binding for blob in request.blobs.blobs),
            )
        prepared = self.stage(admitted, transition, request.blobs)
        committed = self.commit(prepared, prepared.precondition)
        return RepositoryExecutionResult(True, committed, None)

    @staticmethod
    def _projection_identity(
        metadata: os.stat_result,
    ) -> tuple[int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
        )

    def _projection_target(self, relative_path: str) -> Path:
        candidate = PurePosixPath(relative_path)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or candidate.parts[0] == self.root.name
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise FencedCommitError(
                "projection-invalid", "projection target is outside its compatibility root"
            )
        target = self.root.parent.joinpath(*candidate.parts)
        repository_device = self.root.lstat().st_dev
        current = self.root.parent
        for part in candidate.parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir(mode=0o700)
                    metadata = current.lstat()
                except OSError as exc:
                    raise FencedCommitError(
                        "projection-invalid", "projection parent cannot be created safely"
                    ) from exc
            except OSError as exc:
                raise FencedCommitError(
                    "projection-invalid", "projection parent cannot be inspected"
                ) from exc
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_dev != repository_device
            ):
                raise FencedCommitError(
                    "projection-invalid", "projection parent is not a safe same-filesystem directory"
                )
        return target

    def _verify_pinned_projection_target(
        self,
        pinned: _PinnedProjectionTarget,
    ) -> None:
        self._verify_root()
        try:
            root_named = self.root.parent.lstat()
            if (
                _directory_identity(os.fstat(pinned.descriptors[0]))
                != pinned.identities[0]
                or _directory_identity(root_named) != pinned.identities[0]
                or not stat.S_ISDIR(root_named.st_mode)
            ):
                raise FencedCommitError(
                    "repository-changed",
                    "projection root identity changed",
                )
            for index, name in enumerate(pinned.names):
                parent_descriptor = pinned.descriptors[index]
                child_descriptor = pinned.descriptors[index + 1]
                identity = pinned.identities[index + 1]
                named = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _directory_identity(os.fstat(child_descriptor)) != identity
                    or _directory_identity(named) != identity
                    or not stat.S_ISDIR(named.st_mode)
                ):
                    raise FencedCommitError(
                        "repository-changed",
                        "projection parent identity changed",
                    )
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "repository-changed",
                "projection parent identity changed",
            ) from exc

    @contextmanager
    def _pinned_projection_target(self, projection: ProjectionRecord):
        candidate = PurePosixPath(projection.relative_path)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or candidate.parts[0] == self.root.name
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise FencedCommitError(
                "projection-invalid",
                "projection target is outside its compatibility root",
            )
        descriptors = []
        identities = []
        names = tuple(candidate.parts[:-1])
        try:
            descriptor = os.open(
                os.fspath(self.root.parent),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            descriptors.append(descriptor)
            identities.append(_directory_identity(os.fstat(descriptor)))
            for name in names:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)
                opened = os.fstat(descriptor)
                named = os.stat(
                    name,
                    dir_fd=descriptors[-2],
                    follow_symlinks=False,
                )
                identity = _directory_identity(opened)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or _directory_identity(named) != identity
                    or identity[0] != self.root.lstat().st_dev
                ):
                    raise FencedCommitError(
                        "repository-changed",
                        "projection parent cannot be pinned",
                    )
                identities.append(identity)
            pinned = _PinnedProjectionTarget(
                descriptors=tuple(descriptors),
                identities=tuple(identities),
                names=names,
                target_name=candidate.parts[-1],
            )
            if pinned.parent_identity != projection.parent_identity:
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "projection parent differs from its durable identity",
                )
            self._verify_pinned_projection_target(pinned)
            yield pinned
            self._verify_pinned_projection_target(pinned)
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "repository-changed",
                "projection parent cannot be pinned",
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read_pinned_projection_bytes(
        self,
        pinned: _PinnedProjectionTarget,
        *,
        limit: int,
        allow_missing: bool = False,
        allowed_link_counts: tuple[int, ...] = (1,),
        verify_parent: bool = True,
    ) -> Optional[tuple[bytes, tuple[int, int, int, int, int]]]:
        if verify_parent:
            self._verify_pinned_projection_target(pinned)
        try:
            named = os.stat(
                pinned.target_name,
                dir_fd=pinned.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if allow_missing:
                return None
            raise FencedCommitError("projection-invalid", "projection file is missing")
        except OSError as exc:
            raise FencedCommitError(
                "projection-invalid",
                "projection cannot be stated",
            ) from exc
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink not in allowed_link_counts
            or named.st_size > limit
        ):
            raise FencedCommitError(
                "projection-invalid",
                "projection identity is invalid",
            )
        descriptor = None
        try:
            descriptor = os.open(
                pinned.target_name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=pinned.descriptor,
            )
            opened = os.fstat(descriptor)
            expected = _file_identity(named)
            if _file_identity(opened) != expected:
                raise FencedCommitError(
                    "projection-invalid",
                    "projection identity changed",
                )
            chunks = []
            remaining = named.st_size
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise FencedCommitError(
                        "projection-invalid",
                        "projection was truncated",
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise FencedCommitError(
                    "projection-invalid",
                    "projection grew while read",
                )
            final_named = os.stat(
                pinned.target_name,
                dir_fd=pinned.descriptor,
                follow_symlinks=False,
            )
            if (
                _file_identity(os.fstat(descriptor)) != expected
                or _file_identity(final_named) != expected
            ):
                raise FencedCommitError(
                    "projection-invalid",
                    "projection identity changed",
                )
            if verify_parent:
                self._verify_pinned_projection_target(pinned)
            return b"".join(chunks), self._projection_identity(named)
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "projection-invalid",
                "projection cannot be read safely",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_projection_bytes(
        self,
        path: Path,
        *,
        limit: int,
        allow_missing: bool = False,
        allowed_link_counts: tuple[int, ...] = (1,),
    ) -> Optional[tuple[bytes, tuple[int, int, int, int, int]]]:
        try:
            named = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise FencedCommitError("projection-invalid", "projection file is missing")
        except OSError as exc:
            raise FencedCommitError("projection-invalid", "projection cannot be stated") from exc
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink not in allowed_link_counts
            or named.st_size > limit
        ):
            raise FencedCommitError("projection-invalid", "projection identity is invalid")
        descriptor = None
        try:
            descriptor = os.open(
                os.fspath(path),
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            )
            opened = os.fstat(descriptor)
            expected = _file_identity(named)
            if _file_identity(opened) != expected:
                raise FencedCommitError("projection-invalid", "projection identity changed")
            chunks = []
            remaining = named.st_size
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise FencedCommitError("projection-invalid", "projection was truncated")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise FencedCommitError("projection-invalid", "projection grew while read")
            final_opened = os.fstat(descriptor)
            final_named = path.lstat()
            if (
                _file_identity(final_opened) != expected
                or _file_identity(final_named) != expected
            ):
                raise FencedCommitError("projection-invalid", "projection identity changed")
            return b"".join(chunks), self._projection_identity(named)
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError("projection-invalid", "projection cannot be read safely") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _write_projection_after(self, path: Path, content: bytes) -> ProjectionFileRef:
        descriptor = None
        try:
            descriptor = os.open(
                os.fspath(path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise FencedCommitError(
                        "projection-invalid", "projection staging write made no progress"
                    )
                view = view[written:]
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            named = path.lstat()
            if (
                _file_identity(metadata) != _file_identity(named)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size != len(content)
            ):
                raise FencedCommitError(
                    "projection-invalid", "projection staging identity changed"
                )
            return ProjectionFileRef(
                digest=_sha256(content),
                identity=self._projection_identity(metadata),
                name=path.name,
                size=len(content),
            )
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "projection-invalid", "projection staging file cannot be written"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _discard_unprepared_projection_bundle(
        self,
        bundle: Path,
        names: tuple[str, ...],
    ) -> None:
        try:
            for name in names:
                path = bundle / name
                if path.exists() or path.is_symlink():
                    os.unlink(path)
            os.rmdir(bundle)
            descriptor = os.open(os.fspath(bundle.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise FencedCommitError(
                "stage-cleanup-failed", "projection stage cleanup failed"
            ) from exc

    def _stage_projections(
        self,
        transaction_id: str,
        request: ExecutionRequest,
    ) -> tuple[ProjectionRecord, ...]:
        if not request.blobs.blobs:
            return ()
        parent = self.root / "transactions" / "projections"
        try:
            parent_metadata = parent.lstat()
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or stat.S_ISLNK(parent_metadata.st_mode)
                or parent_metadata.st_dev != self.root.lstat().st_dev
            ):
                raise FencedCommitError(
                    "projection-invalid", "projection transaction root is invalid"
                )
            bundle = parent / transaction_id
            bundle.mkdir(mode=0o700)
            os.chmod(bundle, 0o700, follow_symlinks=False)
            bundle_metadata = bundle.lstat()
            if (
                not stat.S_ISDIR(bundle_metadata.st_mode)
                or stat.S_IMODE(bundle_metadata.st_mode) != 0o700
            ):
                raise FencedCommitError(
                    "projection-invalid", "projection bundle is not private"
                )
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "projection-invalid", "projection bundle cannot be created"
            ) from exc
        records = []
        written_names = []
        base_total = 0
        try:
            for index, blob in enumerate(request.blobs.blobs):
                target = self._projection_target(blob.binding.relative_path)
                parent_metadata = target.parent.lstat()
                if (
                    not stat.S_ISDIR(parent_metadata.st_mode)
                    or stat.S_ISLNK(parent_metadata.st_mode)
                    or parent_metadata.st_dev != self.root.lstat().st_dev
                ):
                    raise FencedCommitError(
                        "projection-invalid",
                        "projection parent is not a safe same-filesystem directory",
                    )
                base_value = self._read_projection_bytes(
                    target,
                    limit=STATE_LIMIT,
                    allow_missing=True,
                )
                base = None
                if base_value is not None:
                    base_bytes, base_identity = base_value
                    base_total += len(base_bytes)
                    if base_total > MAX_TOTAL_BLOB_BYTES:
                        raise FencedCommitError(
                            "blob-set-too-large", "projection backups exceed the aggregate limit"
                        )
                    base = ProjectionFileRef(
                        digest=_sha256(base_bytes),
                        identity=base_identity,
                        name="base-%03d.blob" % index,
                        size=len(base_bytes),
                    )
                after_name = "after-%03d.blob" % index
                after = self._write_projection_after(bundle / after_name, blob.content)
                written_names.append(after_name)
                records.append(
                    ProjectionRecord(
                        after=after,
                        base=base,
                        blob_id=blob.binding.blob_id,
                        parent_identity=_directory_identity(parent_metadata),
                        relative_path=blob.binding.relative_path,
                    )
                )
            descriptor = os.open(os.fspath(bundle), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            parent_descriptor = os.open(os.fspath(parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
            return tuple(records)
        except BaseException:
            self._discard_unprepared_projection_bundle(bundle, tuple(written_names))
            raise

    def _stage_persistence(
        self,
        admitted: AdmittedSnapshot,
        *,
        state_bytes: bytes,
        effects: tuple[BlobBinding, ...],
    ) -> PreparedCommit:
        if not isinstance(admitted, AdmittedSnapshot):
            raise FencedCommitError("request-invalid", "admitted snapshot type is invalid")
        if type(state_bytes) is not bytes or type(effects) is not tuple:
            raise FencedCommitError("request-invalid", "stage inputs must be immutable")
        try:
            target_state = decode_mission_state(state_bytes)
        except Exception as exc:
            raise FencedCommitError(getattr(exc, "code", "record-invalid"), "target state is invalid") from exc
        if target_state.identity.session_id is not None and target_state.identity.session_id != admitted.request.session_id:
            raise FencedCommitError("lineage-mismatch", "target state session differs")
        if target_state.lease != admitted.pending_lease.target:
            raise FencedCommitError("pending-lease-mismatch", "target state does not contain the pending lease")
        try:
            staged = stage_generation(
                self.root,
                state_bytes=state_bytes,
                effects=effects,
                blobs=admitted.request.blobs,
            )
        except FencedCommitError:
            raise
        except Exception as exc:
            raise FencedCommitError(getattr(exc, "code", "stage-invalid"), "U1 stage rejected") from exc
        transaction_id = staged.root.name.removeprefix(".stage-")
        if _TRANSACTION_RE.fullmatch(transaction_id) is None:
            try:
                discard_staged_generation(self.root, staged)
            finally:
                raise FencedCommitError("stage-invalid", "U1 transaction ID is invalid")
        try:
            projections = self._stage_projections(transaction_id, admitted.request)
            provisional = PreparedCommit(
                admitted=admitted,
                staged=staged,
                target_state=target_state,
                state_bytes=state_bytes,
                effects=effects,
                projections=projections,
                transaction_id=transaction_id,
                precondition=admitted.precondition,
                binding_digest="sha256:" + "0" * 64,
            )
        except BaseException:
            try:
                discard_staged_generation(self.root, staged)
            except (LocalUnitOfWorkError, OSError) as cleanup_exc:
                raise FencedCommitError(
                    "stage-cleanup-failed", "stage cleanup failed after projection rejection"
                ) from cleanup_exc
            raise
        prepared = replace(
            provisional,
            binding_digest=_prepared_binding_digest(provisional),
        )
        if transaction_id in self._stage_binding_registry:
            try:
                if prepared.projections:
                    self._discard_unprepared_projection_bundle(
                        self.root
                        / "transactions"
                        / "projections"
                        / transaction_id,
                        tuple(
                            projection.after.name
                            for projection in prepared.projections
                        ),
                    )
                discard_staged_generation(self.root, prepared.staged)
            except (FencedCommitError, LocalUnitOfWorkError, OSError) as exc:
                raise FencedCommitError(
                    "stage-cleanup-failed",
                    "duplicate private stage cleanup failed",
                ) from exc
            raise FencedCommitError(
                "stage-invalid", "private stage transaction is already registered"
            )
        self._stage_binding_registry[transaction_id] = prepared.binding_digest
        self._fault("after-stage")
        return prepared

    def _recovery_report_unlocked(self, session_id: str) -> RecoveryReport:
        head, head_bytes, head_digest = self._read_head_unlocked(session_id)
        if head is None:
            return RecoveryReport(session_id, True, 0, None, None)
        assert head_bytes is not None and head_digest is not None
        snapshot = self._read_snapshot_from_head_unlocked(
            session_id,
            head,
            head_bytes,
            head_digest,
        )
        return RecoveryReport(
            session_id,
            True,
            snapshot.head.generation,
            snapshot.head_digest,
            snapshot.result.commit_digest,
        )

    def _recover_orphan_stages_unlocked(self) -> None:
        with self._pinned_directory("transactions") as pinned:
            try:
                entries = sorted(os.listdir(pinned.descriptor))
            except OSError as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "transaction directory cannot be inspected"
                ) from exc
            self._verify_pinned_directory(pinned)
        for name in entries:
            if name in {
                "prepared",
                "projections",
                "quarantine",
                "resolved",
                "resolved-operations",
            }:
                continue
            if not name.startswith(".stage-") or _TRANSACTION_RE.fullmatch(
                name.removeprefix(".stage-")
            ) is None:
                raise FencedCommitError(
                    "recovery-ambiguous", "unexpected transaction residue blocks recovery"
                )
            transaction_id = name.removeprefix(".stage-")
            try:
                staged = load_staged_generation(self.root, transaction_id)
                self._discard_orphan_projection_bundle_unlocked(
                    transaction_id,
                    staged,
                )
                discard_staged_generation(self.root, staged)
            except (LocalUnitOfWorkError, OSError) as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "private stage residue is not verifiable"
                ) from exc
        projection_root = self.root / "transactions" / "projections"
        try:
            if any(projection_root.iterdir()):
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "projection bundle without a private stage is ambiguous",
                )
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "recovery-ambiguous", "projection transaction root cannot be inspected"
            ) from exc

    def _discard_orphan_projection_bundle_unlocked(
        self,
        transaction_id: str,
        staged: StagedGeneration,
    ) -> None:
        _state, effects = self._manifest_records(staged.manifest_bytes)
        bundle = (
            self.root / "transactions" / "projections" / transaction_id
        )
        if not effects:
            if bundle.exists() or bundle.is_symlink():
                raise FencedCommitError(
                    "recovery-ambiguous", "empty stage has a projection bundle"
                )
            return
        try:
            metadata = bundle.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_dev != self.root.lstat().st_dev
            ):
                raise FencedCommitError(
                    "recovery-ambiguous", "orphan projection bundle is invalid"
                )
            expected_names = {"after-%03d.blob" % index for index in range(len(effects))}
            if {path.name for path in bundle.iterdir()} != expected_names:
                raise FencedCommitError(
                    "recovery-ambiguous", "orphan projection bundle entries differ"
                )
            for index, effect in enumerate(effects):
                path = bundle / ("after-%03d.blob" % index)
                file_metadata = path.lstat()
                reference = ProjectionFileRef(
                    digest=effect.digest,
                    identity=self._projection_identity(file_metadata),
                    name=path.name,
                    size=effect.size,
                )
                self._require_projection_ref(
                    path,
                    reference,
                    allowed_link_counts=(1,),
                )
            self._discard_unprepared_projection_bundle(
                bundle,
                tuple(sorted(expected_names)),
            )
        except FencedCommitError:
            raise
        except OSError as exc:
            raise FencedCommitError(
                "recovery-ambiguous", "orphan projection bundle cannot be verified"
            ) from exc

    def _read_prepare_unlocked(self, name: str) -> tuple[PrepareRecord, bytes]:
        with self._pinned_directory("transactions", "prepared") as pinned:
            content = self._read_pinned_file(
                pinned,
                name,
                limit=MAX_PREPARE_BYTES,
            )
        assert content is not None
        try:
            return _parse_prepare(content, name), content
        except FencedCommitError as exc:
            raise FencedCommitError(
                "recovery-ambiguous", "durable prepare is malformed"
            ) from exc

    def _resolution_document(
        self,
        prepare: PrepareRecord,
        disposition: str,
        report: RecoveryReport,
    ) -> dict:
        if disposition not in {"rolled-back", "finalized"}:
            raise FencedCommitError(
                "recovery-ambiguous", "recovery disposition is invalid"
            )
        return {
            "base_generation": prepare.base.generation,
            "disposition": disposition,
            "head_digest": report.head_digest,
            "intent_digest": prepare.intent_digest,
            "operation_id": prepare.operation_id,
            "schema": "mission-recovery/1",
            "session_id": prepare.session_id,
            "target_generation": prepare.target_generation,
            "transaction_id": prepare.transaction_id,
        }

    def _publish_resolution_unlocked(
        self,
        prepare: PrepareRecord,
        disposition: str,
        report: RecoveryReport,
    ) -> None:
        content = _canonical_bytes(
            self._resolution_document(prepare, disposition, report),
            limit=MAX_OPERATION_BYTES,
        )
        self._publish_named_immutable(
            self.root
            / "transactions"
            / "resolved"
            / (prepare.transaction_id + ".json"),
            content,
            limit=MAX_OPERATION_BYTES,
            collision_code="recovery-ambiguous",
        )
        resolution = _parse_recovery_resolution(
            content,
            transaction_filename=prepare.transaction_id + ".json",
        )
        self._ensure_resolution_index_unlocked()
        self._publish_resolution_index_unlocked(resolution)

    def _resolution_exists_unlocked(
        self,
        prepare: PrepareRecord,
        disposition: str,
        report: RecoveryReport,
    ) -> bool:
        expected = _canonical_bytes(
            self._resolution_document(prepare, disposition, report),
            limit=MAX_OPERATION_BYTES,
        )
        with self._pinned_directory("transactions", "resolved") as pinned:
            current = self._read_pinned_file(
                pinned,
                prepare.transaction_id + ".json",
                limit=MAX_OPERATION_BYTES,
                allow_missing=True,
            )
        if current is None:
            return False
        if current != expected:
            raise FencedCommitError(
                "recovery-ambiguous",
                "recovery resolution differs from durable lineage",
            )
        return True

    def _prepare_matches_stage_or_generation_unlocked(
        self,
        prepare: PrepareRecord,
    ) -> Optional[StagedGeneration]:
        stage_path = (
            self.root / "transactions" / (".stage-" + prepare.transaction_id)
        )
        if stage_path.exists() or stage_path.is_symlink():
            try:
                staged = load_staged_generation(self.root, prepare.transaction_id)
            except (LocalUnitOfWorkError, OSError) as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "prepared private stage is not verifiable"
                ) from exc
            if (
                staged.generation_digest != prepare.generation.digest
                or len(staged.manifest_bytes) != prepare.generation.size
            ):
                raise FencedCommitError(
                    "recovery-ambiguous", "prepared stage differs from journal"
                )
            state, effects = self._manifest_records(staged.manifest_bytes)
            if state != prepare.state or effects != prepare.effects:
                raise FencedCommitError(
                    "recovery-ambiguous", "prepared stage lineage differs"
                )
            return staged
        try:
            manifest = self._read_exact(prepare.generation, limit=STATE_LIMIT)
        except FencedCommitError as exc:
            raise FencedCommitError(
                "recovery-ambiguous", "prepared generation has no verifiable bytes"
            ) from exc
        state, effects = self._manifest_records(manifest)
        if state != prepare.state or effects != prepare.effects:
            raise FencedCommitError(
                "recovery-ambiguous", "prepared immutable lineage differs"
            )
        return None

    def _head_is_prepare_base_unlocked(self, prepare: PrepareRecord) -> bool:
        head, head_bytes, head_digest = self._read_head_unlocked(prepare.session_id)
        if prepare.base.generation == 0:
            return head is None and head_bytes is None and head_digest is None
        if (
            head is None
            or head_bytes is None
            or head_digest is None
            or head.generation != prepare.base.generation
            or head_digest != prepare.base.head_digest
        ):
            return False
        self._read_snapshot_from_head_unlocked(
            prepare.session_id,
            head,
            head_bytes,
            head_digest,
        )
        return True

    def _target_snapshot_unlocked(
        self,
        prepare: PrepareRecord,
    ) -> Optional[RepositorySnapshot]:
        head, head_bytes, head_digest = self._read_head_unlocked(prepare.session_id)
        if (
            head is None
            or head_bytes is None
            or head_digest is None
            or head.generation != prepare.target_generation
        ):
            return None
        snapshot = self._read_snapshot_from_head_unlocked(
            prepare.session_id,
            head,
            head_bytes,
            head_digest,
        )
        commit = snapshot.commit
        if (
            commit.audit != prepare.audit
            or commit.base != prepare.base
            or commit.committed_at != prepare.prepared_at
            or commit.effects != prepare.effects
            or commit.fencing_epoch != prepare.fencing_epoch
            or commit.generation != prepare.generation
            or commit.intent_digest != prepare.intent_digest
            or commit.operation_id != prepare.operation_id
            or commit.session_id != prepare.session_id
            or commit.state != prepare.state
            or commit.target_generation != prepare.target_generation
            or commit.transaction_id != prepare.transaction_id
        ):
            raise FencedCommitError(
                "recovery-ambiguous", "target head and durable prepare disagree"
            )
        return snapshot

    def _verify_or_publish_operation_unlocked(
        self,
        prepare: PrepareRecord,
        snapshot: RepositorySnapshot,
    ) -> None:
        expected_document = {
            "commit_digest": snapshot.result.commit_digest,
            "intent_digest": prepare.intent_digest,
            "operation_id": prepare.operation_id,
            "result": _result_document(snapshot.result),
            "schema": "mission-operation/1",
            "session_id": prepare.session_id,
        }
        expected = _canonical_bytes(expected_document, limit=MAX_OPERATION_BYTES)
        path = self._operation_path(prepare.session_id, prepare.operation_id)
        with self._pinned_directory("operations") as pinned:
            existing = self._read_pinned_file(
                pinned,
                path.name,
                limit=MAX_OPERATION_BYTES,
                allow_missing=True,
            )
        if existing is not None and existing != expected:
            raise FencedCommitError(
                "recovery-ambiguous", "operation record disagrees with target lineage"
            )
        if existing is None:
            self._publish_named_immutable(
                path,
                expected,
                limit=MAX_OPERATION_BYTES,
                collision_code="recovery-ambiguous",
            )

    def _recover_target_unlocked(
        self,
        prepare: PrepareRecord,
        snapshot: RepositorySnapshot,
        prepare_path: Path,
        prepare_bytes: bytes,
    ) -> RecoveryReport:
        self._prepare_matches_stage_or_generation_unlocked(prepare)
        report = self._recovery_report_unlocked(prepare.session_id)
        cleanup_started = self._resolution_exists_unlocked(
            prepare,
            "finalized",
            report,
        )
        self._rollforward_projections_unlocked(
            prepare,
            allow_cleanup_residue=cleanup_started,
        )
        self._verify_or_publish_operation_unlocked(prepare, snapshot)
        self._publish_resolution_unlocked(prepare, "finalized", report)
        self._fault("after-resolution-marker")
        self._cleanup_projection_bundle_unlocked(prepare, target_is_authoritative=True)
        stage_path = (
            self.root / "transactions" / (".stage-" + prepare.transaction_id)
        )
        if stage_path.exists() or stage_path.is_symlink():
            try:
                staged = load_staged_generation(self.root, prepare.transaction_id)
                discard_staged_generation(self.root, staged)
            except (LocalUnitOfWorkError, OSError) as exc:
                raise FencedCommitError(
                    "recovery-blocked", "verified private stage cleanup failed"
                ) from exc
        self._remove_exact(
            prepare_path,
            prepare_bytes,
            limit=MAX_PREPARE_BYTES,
        )
        return report

    def _rollforward_projections_unlocked(
        self,
        prepare: PrepareRecord,
        *,
        allow_cleanup_residue: bool = False,
    ) -> None:
        if not prepare.projections:
            return
        bundle = (
            self.root / "transactions" / "projections" / prepare.transaction_id
        )
        if not bundle.exists() and not bundle.is_symlink():
            if not allow_cleanup_residue:
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "projection recovery bundle is missing",
                )
            for projection in prepare.projections:
                with self._pinned_projection_target(projection) as pinned:
                    current = self._read_pinned_projection_bytes(
                        pinned,
                        limit=STATE_LIMIT,
                        allowed_link_counts=(1,),
                    )
                    if not self._projection_value_matches(
                        current,
                        projection.after,
                    ):
                        raise FencedCommitError(
                            "recovery-ambiguous",
                            "finalized projection differs after cleanup",
                        )
            return
        for index, projection in enumerate(prepare.projections):
            after_path = bundle / projection.after.name
            if after_path.exists() or after_path.is_symlink():
                self._require_projection_ref(
                    after_path,
                    projection.after,
                    allowed_link_counts=(1, 2),
                )
            elif not allow_cleanup_residue:
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "projection recovery source is missing",
                )
            base_path = bundle / ("base-%03d.blob" % index)
            if projection.base is not None:
                if base_path.exists() or base_path.is_symlink():
                    self._require_projection_ref(
                        base_path,
                        projection.base,
                        allowed_link_counts=(1,),
                    )
                elif not allow_cleanup_residue:
                    raise FencedCommitError(
                        "recovery-ambiguous",
                        "projection base backup is missing",
                    )
            elif base_path.exists() or base_path.is_symlink():
                raise FencedCommitError(
                    "recovery-ambiguous", "new projection has an unexpected base backup"
                )
            with self._pinned_projection_target(projection) as pinned:
                current = self._read_pinned_projection_bytes(
                    pinned,
                    limit=STATE_LIMIT,
                    allow_missing=True,
                    allowed_link_counts=(1, 2),
                )
                if current is None:
                    linked = False
                    try:
                        os.link(
                            os.fspath(after_path),
                            pinned.target_name,
                            dst_dir_fd=pinned.descriptor,
                            follow_symlinks=False,
                        )
                        linked = True
                        self._verify_pinned_projection_target(pinned)
                    except BaseException:
                        if linked:
                            try:
                                published = self._read_pinned_projection_bytes(
                                    pinned,
                                    limit=STATE_LIMIT,
                                    allowed_link_counts=(2,),
                                    verify_parent=False,
                                )
                                if self._projection_value_matches(
                                    published,
                                    projection.after,
                                ):
                                    os.unlink(
                                        pinned.target_name,
                                        dir_fd=pinned.descriptor,
                                    )
                                    os.fsync(pinned.descriptor)
                            except (FencedCommitError, OSError):
                                pass
                        raise
                    os.fsync(pinned.descriptor)
                    self._fsync_projection_directory(bundle)
                elif not self._projection_value_matches(current, projection.after):
                    raise FencedCommitError(
                        "recovery-ambiguous",
                        "target projection was replaced after commit",
                    )
                published = self._read_pinned_projection_bytes(
                    pinned,
                    limit=STATE_LIMIT,
                    allowed_link_counts=(
                        (1, 2) if allow_cleanup_residue else (2,)
                    ),
                )
                if not self._projection_value_matches(published, projection.after):
                    raise FencedCommitError(
                        "recovery-ambiguous",
                        "target projection differs after recovery",
                    )
            if after_path.exists() or after_path.is_symlink():
                self._require_projection_ref(
                    after_path,
                    projection.after,
                    allowed_link_counts=(
                        (1, 2) if allow_cleanup_residue else (2,)
                    ),
                )
            elif not allow_cleanup_residue:
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "projection recovery source is missing",
                )

    def _recover_base_unlocked(
        self,
        prepare: PrepareRecord,
        prepare_path: Path,
        prepare_bytes: bytes,
    ) -> RecoveryReport:
        staged = self._prepare_matches_stage_or_generation_unlocked(prepare)
        operation_path = self._operation_path(
            prepare.session_id,
            prepare.operation_id,
        )
        with self._pinned_directory("operations") as pinned:
            operation = self._read_pinned_file(
                pinned,
                operation_path.name,
                limit=MAX_OPERATION_BYTES,
                allow_missing=True,
            )
        if operation is not None:
            raise FencedCommitError(
                "recovery-ambiguous",
                "base head conflicts with an existing operation record",
            )
        report = self._recovery_report_unlocked(prepare.session_id)
        cleanup_started = self._resolution_exists_unlocked(
            prepare,
            "rolled-back",
            report,
        )
        if cleanup_started:
            self._verify_rolled_back_projections_unlocked(prepare)
        else:
            self._rollback_projections_unlocked(prepare)
        self._publish_resolution_unlocked(prepare, "rolled-back", report)
        self._fault("after-resolution-marker")
        self._cleanup_projection_bundle_unlocked(prepare, target_is_authoritative=False)
        if staged is not None:
            try:
                discard_staged_generation(self.root, staged)
            except (LocalUnitOfWorkError, OSError) as exc:
                raise FencedCommitError(
                    "recovery-blocked", "verified private stage cleanup failed"
                ) from exc
        self._remove_exact(
            prepare_path,
            prepare_bytes,
            limit=MAX_PREPARE_BYTES,
        )
        return report

    def _verify_rolled_back_projections_unlocked(
        self,
        prepare: PrepareRecord,
    ) -> None:
        bundle = (
            self.root / "transactions" / "projections" / prepare.transaction_id
        )
        for index, projection in enumerate(prepare.projections):
            target = self._projection_target(projection.relative_path)
            if projection.base is None:
                if target.exists() or target.is_symlink():
                    raise FencedCommitError(
                        "recovery-ambiguous",
                        "rolled-back new projection still exists",
                    )
            else:
                self._require_projection_ref(
                    target,
                    projection.base,
                    allowed_link_counts=(1,),
                )
            base_path = bundle / ("base-%03d.blob" % index)
            if base_path.exists() or base_path.is_symlink():
                raise FencedCommitError(
                    "recovery-ambiguous",
                    "rolled-back projection base residue remains",
                )

    @staticmethod
    def _projection_value_matches(
        value: Optional[tuple[bytes, tuple[int, int, int, int, int]]],
        reference: ProjectionFileRef,
    ) -> bool:
        if value is None:
            return False
        content, identity = value
        return (
            identity == reference.identity
            and len(content) == reference.size
            and _sha256(content) == reference.digest
        )

    def _rollback_projections_unlocked(self, prepare: PrepareRecord) -> None:
        if not prepare.projections:
            return
        bundle = (
            self.root / "transactions" / "projections" / prepare.transaction_id
        )
        for index, projection in enumerate(prepare.projections):
            after_path = bundle / projection.after.name
            self._require_projection_ref(
                after_path,
                projection.after,
                allowed_link_counts=(1, 2),
            )
            target = self._projection_target(projection.relative_path)
            current = self._read_projection_bytes(
                target,
                limit=STATE_LIMIT,
                allow_missing=True,
                allowed_link_counts=(1, 2),
            )
            base_path = bundle / ("base-%03d.blob" % index)
            base_backup = self._read_projection_bytes(
                base_path,
                limit=STATE_LIMIT,
                allow_missing=True,
                allowed_link_counts=(1,),
            )
            if projection.base is None:
                if current is not None:
                    if not self._projection_value_matches(current, projection.after):
                        raise FencedCommitError(
                            "recovery-ambiguous", "created projection was replaced"
                        )
                    os.unlink(target)
                    self._fsync_projection_directory(target.parent)
                if base_backup is not None:
                    raise FencedCommitError(
                        "recovery-ambiguous", "new projection has an unexpected base backup"
                    )
            elif self._projection_value_matches(current, projection.base):
                if base_backup is not None:
                    raise FencedCommitError(
                        "recovery-ambiguous", "base projection has a duplicate private inode"
                    )
            else:
                if current is not None:
                    if not self._projection_value_matches(current, projection.after):
                        raise FencedCommitError(
                            "recovery-ambiguous", "published projection identity is ambiguous"
                        )
                    os.unlink(target)
                    self._fsync_projection_directory(target.parent)
                self._fault("during-projection-rollback:%d" % index)
                if not self._projection_value_matches(base_backup, projection.base):
                    raise FencedCommitError(
                        "recovery-blocked", "exact projection backup is unavailable"
                    )
                try:
                    os.rename(os.fspath(base_path), os.fspath(target))
                except OSError as exc:
                    raise FencedCommitError(
                        "recovery-blocked", "exact projection backup cannot be restored"
                    ) from exc
                self._fsync_projection_directory(bundle)
                self._fsync_projection_directory(target.parent)
            if projection.base is None:
                if target.exists() or target.is_symlink():
                    raise FencedCommitError(
                        "recovery-blocked", "created projection still exists after rollback"
                    )
            else:
                self._require_projection_ref(
                    target,
                    projection.base,
                    allowed_link_counts=(1,),
                )

    def _cleanup_projection_bundle_unlocked(
        self,
        prepare: PrepareRecord,
        *,
        target_is_authoritative: bool,
    ) -> None:
        if not prepare.projections:
            return
        bundle = (
            self.root / "transactions" / "projections" / prepare.transaction_id
        )
        if not bundle.exists() and not bundle.is_symlink():
            return
        for index, projection in enumerate(prepare.projections):
            target = self._projection_target(projection.relative_path)
            after_path = bundle / projection.after.name
            if target_is_authoritative:
                self._require_projection_ref(
                    target,
                    projection.after,
                    allowed_link_counts=(1, 2),
                )
                base_path = bundle / ("base-%03d.blob" % index)
                if projection.base is not None:
                    if base_path.exists() or base_path.is_symlink():
                        self._require_projection_ref(
                            base_path,
                            projection.base,
                            allowed_link_counts=(1,),
                        )
                        os.unlink(base_path)
                        self._fsync_projection_directory(bundle)
                        self._fault(
                            "during-projection-cleanup-base:%d" % index
                        )
                if after_path.exists() or after_path.is_symlink():
                    self._require_projection_ref(
                        after_path,
                        projection.after,
                        allowed_link_counts=(2,),
                    )
                else:
                    self._require_projection_ref(
                        target,
                        projection.after,
                        allowed_link_counts=(1,),
                    )
            else:
                if after_path.exists() or after_path.is_symlink():
                    self._require_projection_ref(
                        after_path,
                        projection.after,
                        allowed_link_counts=(1,),
                    )
                base_path = bundle / ("base-%03d.blob" % index)
                if base_path.exists() or base_path.is_symlink():
                    raise FencedCommitError(
                        "recovery-blocked", "projection base residue remains after rollback"
                    )
            if after_path.exists() or after_path.is_symlink():
                os.unlink(after_path)
                self._fsync_projection_directory(bundle)
                self._fault(
                    "during-projection-cleanup-after:%d" % index
                )
            if target_is_authoritative:
                self._require_projection_ref(
                    target,
                    projection.after,
                    allowed_link_counts=(1,),
                )
        try:
            os.rmdir(bundle)
            self._fsync_projection_directory(bundle.parent)
        except OSError as exc:
            raise FencedCommitError(
                "recovery-blocked", "projection bundle cleanup failed"
            ) from exc

    def _prepared_entries_unlocked(self) -> list[str]:
        with self._pinned_directory("transactions", "prepared") as pinned:
            try:
                prepared_entries = sorted(os.listdir(pinned.descriptor))
            except OSError as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "prepare directory cannot be inspected"
                ) from exc
            self._verify_pinned_directory(pinned)
        return prepared_entries

    def _recover_unlocked(self, session_id: str) -> RecoveryReport:
        prepared_entries = self._prepared_entries_unlocked()
        if len(prepared_entries) > 1:
            raise FencedCommitError(
                "recovery-ambiguous", "multiple durable prepares cannot be ordered"
            )
        if prepared_entries:
            name = prepared_entries[0]
            prepare, prepare_bytes = self._read_prepare_unlocked(name)
            if prepare.session_id != session_id:
                raise FencedCommitError(
                    "recovery-ambiguous", "durable prepare belongs to another session"
                )
            prepare_path = self.root / "transactions" / "prepared" / name
            try:
                if self._head_is_prepare_base_unlocked(prepare):
                    report = self._recover_base_unlocked(
                        prepare,
                        prepare_path,
                        prepare_bytes,
                    )
                else:
                    target = self._target_snapshot_unlocked(prepare)
                    if target is None:
                        raise FencedCommitError(
                            "recovery-ambiguous",
                            "head is neither the prepared base nor target",
                        )
                    report = self._recover_target_unlocked(
                        prepare,
                        target,
                        prepare_path,
                        prepare_bytes,
                    )
            except FencedCommitError:
                raise
            except Exception as exc:
                raise FencedCommitError(
                    "recovery-ambiguous", "durable transaction cannot be classified"
                ) from exc
            self._recover_orphan_stages_unlocked()
            return report
        self._recover_orphan_stages_unlocked()
        return self._recovery_report_unlocked(session_id)

    def recover(self, session_id: str) -> RecoveryReport:
        session_id = _session_id(session_id)
        with self._lock():
            return self._recover_unlocked(session_id)

    def collect(self, policy: RetentionPolicy = RetentionPolicy()) -> GCReport:
        with self._lock():
            try:
                return collect_locked(self, policy)
            except GarbageCollectionError as exc:
                raise FencedCommitError(exc.code, exc.detail) from exc

    def _write_temp(self, pinned: _PinnedDirectory, content: bytes) -> str:
        self._verify_pinned_directory(pinned)
        name = ""
        descriptor = None
        for _ in range(32):
            candidate = ".u2-" + secrets.token_hex(16) + ".tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=pinned.descriptor,
                )
            except FileExistsError:
                continue
            except OSError as exc:
                raise FencedCommitError(
                    "record-write-failed", "temporary record cannot be created safely"
                ) from exc
            name = candidate
            break
        if descriptor is None or not name:
            raise FencedCommitError("record-write-failed", "temporary record name exhausted")
        try:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            named = os.stat(name, dir_fd=pinned.descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or _file_identity(metadata) != _file_identity(named)
            ):
                raise FencedCommitError("record-write-failed", "temporary record identity changed")
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise FencedCommitError("record-write-failed", "record write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            final_metadata = os.fstat(descriptor)
            final_named = os.stat(name, dir_fd=pinned.descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(final_metadata.st_mode)
                or final_metadata.st_nlink != 1
                or _file_identity(final_metadata) != _file_identity(final_named)
                or final_metadata.st_size != len(content)
            ):
                raise FencedCommitError("record-write-failed", "temporary record identity changed")
            self._verify_pinned_directory(pinned)
            return name
        except BaseException:
            try:
                os.unlink(name, dir_fd=pinned.descriptor)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)

    def _path_parts(self, path: Path) -> tuple[tuple[str, ...], str]:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise FencedCommitError("repository-invalid", "record path escapes repository") from exc
        parts = relative.parts
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise FencedCommitError("repository-invalid", "record path is unsafe")
        return tuple(parts[:-1]), parts[-1]

    def _publish_named_immutable(self, path: Path, content: bytes, *, limit: int, collision_code: str) -> None:
        if len(content) > limit:
            raise FencedCommitError("record-too-large", "immutable record exceeds its limit")
        directory_parts, name = self._path_parts(path)
        with self._pinned_directory(*directory_parts) as pinned:
            try:
                existing = self._read_pinned_file(
                    pinned,
                    name,
                    limit=limit,
                    allow_missing=True,
                )
            except FencedCommitError as exc:
                raise FencedCommitError(
                    collision_code, "immutable record collision is unreadable"
                ) from exc
            if existing is not None:
                if existing != content:
                    raise FencedCommitError(collision_code, "immutable record collision differs")
                return
            temporary = self._write_temp(pinned, content)
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=pinned.descriptor,
                    dst_dir_fd=pinned.descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                existing = self._read_pinned_file(pinned, name, limit=limit)
                if existing != content:
                    raise FencedCommitError(collision_code, "immutable record race differs")
            finally:
                try:
                    os.unlink(temporary, dir_fd=pinned.descriptor)
                except FileNotFoundError:
                    pass
            os.fsync(pinned.descriptor)
            published = self._read_pinned_file(pinned, name, limit=limit)
            if published != content:
                raise FencedCommitError(collision_code, "immutable record publication differs")
            self._verify_pinned_directory(pinned)

    def _replace_head(
        self,
        session_id: str,
        content: bytes,
        before_replace: Optional[Callable[[], None]] = None,
    ) -> None:
        with self._pinned_directory("sessions") as pinned:
            temporary = self._write_temp(pinned, content)
            try:
                if before_replace is not None:
                    before_replace()
                os.replace(
                    temporary,
                    session_id + ".json",
                    src_dir_fd=pinned.descriptor,
                    dst_dir_fd=pinned.descriptor,
                )
            finally:
                try:
                    os.unlink(temporary, dir_fd=pinned.descriptor)
                except FileNotFoundError:
                    pass
            os.fsync(pinned.descriptor)
            published = self._read_pinned_file(
                pinned,
                session_id + ".json",
                limit=MAX_HEAD_BYTES,
            )
            if published != content:
                raise FencedCommitError("head-write-failed", "head replacement differs")
            self._verify_pinned_directory(pinned)

    def _remove_exact(self, path: Path, expected: bytes, *, limit: int) -> None:
        directory_parts, name = self._path_parts(path)
        with self._pinned_directory(*directory_parts) as pinned:
            current = self._read_pinned_file(pinned, name, limit=limit)
            if current != expected:
                raise FencedCommitError("lineage-mismatch", "record changed before removal")
            try:
                os.unlink(name, dir_fd=pinned.descriptor)
            except OSError as exc:
                raise FencedCommitError("record-write-failed", "record cannot be removed") from exc
            os.fsync(pinned.descriptor)
            if self._read_pinned_file(
                pinned,
                name,
                limit=limit,
                allow_missing=True,
            ) is not None:
                raise FencedCommitError("record-write-failed", "record removal did not persist")

    def _current_cas(self, prepared: PreparedCommit) -> tuple[Optional[RepositorySnapshot], Optional[str]]:
        head, head_bytes, digest = self._read_head_unlocked(prepared.admitted.request.session_id)
        if head is None:
            current = None
            generation = 0
        else:
            assert head_bytes is not None and digest is not None
            current = self._read_snapshot_from_head_unlocked(
                prepared.admitted.request.session_id,
                head,
                head_bytes,
                digest,
            )
            generation = current.head.generation
        if (
            generation != prepared.precondition.base_generation
            or digest != prepared.precondition.base_head_digest
        ):
            raise FencedCommitError("head-cas-mismatch", "base generation or head digest moved")
        return current, digest

    def _invalidate_stage_binding(self, prepared: PreparedCommit) -> bool:
        if not isinstance(prepared, PreparedCommit):
            return False
        candidates = set()
        if isinstance(prepared.transaction_id, str):
            candidates.add(prepared.transaction_id)
        if isinstance(prepared.staged, StagedGeneration):
            root_name = prepared.staged.root.name
            if root_name.startswith(".stage-"):
                candidates.add(root_name.removeprefix(".stage-"))
        binding_digest = prepared.binding_digest
        invalidated = False
        for transaction_id, stored_digest in tuple(
            self._stage_binding_registry.items()
        ):
            if transaction_id in candidates or stored_digest == binding_digest:
                del self._stage_binding_registry[transaction_id]
                invalidated = True
        return invalidated

    def _discard_on_reject(
        self,
        prepared: PreparedCommit,
        cause: Optional[BaseException] = None,
    ) -> None:
        if not self._invalidate_stage_binding(prepared):
            return
        try:
            if prepared.projections:
                bundle = (
                    self.root
                    / "transactions"
                    / "projections"
                    / prepared.transaction_id
                )
                for index, projection in enumerate(prepared.projections):
                    base_path = bundle / ("base-%03d.blob" % index)
                    if base_path.exists() or base_path.is_symlink():
                        raise FencedCommitError(
                            "stage-cleanup-failed",
                            "pre-prepare projection stage contains a base backup",
                        )
                    self._require_projection_ref(
                        bundle / projection.after.name,
                        projection.after,
                        allowed_link_counts=(1,),
                    )
                self._discard_unprepared_projection_bundle(
                    bundle,
                    tuple(projection.after.name for projection in prepared.projections),
                )
            discard_staged_generation(self.root, prepared.staged)
        except (FencedCommitError, LocalUnitOfWorkError, OSError) as exc:
            cleanup_error = FencedCommitError(
                "stage-cleanup-failed",
                "private stage cleanup failed after commit rejection",
            )
            if cause is not None:
                raise cleanup_error from cause
            raise cleanup_error from exc

    def _validate_prepared_binding(self, prepared: PreparedCommit) -> None:
        if not isinstance(prepared, PreparedCommit):
            raise FencedCommitError(
                "precondition-mismatch", "prepared commit type is invalid"
            )
        if not isinstance(prepared.admitted, AdmittedSnapshot) or not isinstance(
            prepared.staged, StagedGeneration
        ):
            raise FencedCommitError(
                "precondition-mismatch", "prepared admission or stage type is invalid"
            )
        if type(prepared.state_bytes) is not bytes or type(prepared.effects) is not tuple:
            raise FencedCommitError(
                "precondition-mismatch", "prepared state and effects are not immutable"
            )
        if (
            not isinstance(prepared.transaction_id, str)
            or _TRANSACTION_RE.fullmatch(prepared.transaction_id) is None
        ):
            raise FencedCommitError(
                "precondition-mismatch", "prepared transaction ID is invalid"
            )
        validate_execution_request(prepared.admitted.request)
        _digest(prepared.binding_digest, "prepared.binding_digest")
        stored_digest = self._stage_binding_registry.get(prepared.transaction_id)
        if (
            stored_digest is None
            or stored_digest != prepared.binding_digest
            or _prepared_binding_digest(prepared) != prepared.binding_digest
        ):
            raise FencedCommitError(
                "precondition-mismatch",
                "prepared request or same-instance stage authority changed",
            )
        if prepared.precondition != prepared.admitted.precondition:
            raise FencedCommitError(
                "precondition-mismatch", "admission precondition differs from stage"
            )
        if prepared.admitted.target_generation != prepared.precondition.base_generation + 1:
            raise FencedCommitError(
                "precondition-mismatch", "target generation is not N+1"
            )
        if prepared.staged.root.name != ".stage-" + prepared.transaction_id:
            raise FencedCommitError(
                "precondition-mismatch", "transaction ID differs from private stage"
            )
        try:
            decoded_state = decode_mission_state(prepared.state_bytes)
        except Exception as exc:
            raise FencedCommitError(
                getattr(exc, "code", "record-invalid"), "prepared state is invalid"
            ) from exc
        if decoded_state != prepared.target_state:
            raise FencedCommitError(
                "precondition-mismatch", "prepared target differs from state bytes"
            )
        if decoded_state.lease != prepared.admitted.pending_lease.target:
            raise FencedCommitError(
                "pending-lease-mismatch", "prepared state lease changed"
            )
        request_effects = tuple(
            blob.binding for blob in prepared.admitted.request.blobs.blobs
        )
        if prepared.effects != request_effects:
            raise FencedCommitError(
                "precondition-mismatch", "prepared effects differ from request blobs"
            )
        if len(prepared.projections) != len(prepared.effects):
            raise FencedCommitError(
                "precondition-mismatch", "prepared projections differ from effects"
            )
        bundle = (
            self.root
            / "transactions"
            / "projections"
            / prepared.transaction_id
        )
        for index, (projection, effect) in enumerate(
            zip(prepared.projections, prepared.effects)
        ):
            if (
                projection.blob_id != effect.blob_id
                or projection.relative_path != effect.relative_path
                or projection.after.digest != effect.digest
                or projection.after.size != effect.size
                or projection.after.name != "after-%03d.blob" % index
                or (
                    projection.base is not None
                    and projection.base.name != "base-%03d.blob" % index
                )
            ):
                raise FencedCommitError(
                    "precondition-mismatch", "prepared projection binding changed"
                )
            self._require_projection_ref(
                bundle / projection.after.name,
                projection.after,
                allowed_link_counts=(1,),
            )
            with self._pinned_projection_target(projection) as pinned:
                current = self._read_pinned_projection_bytes(
                    pinned,
                    limit=STATE_LIMIT,
                    allow_missing=True,
                    allowed_link_counts=(1,),
                )
                if projection.base is None:
                    if current is not None:
                        raise FencedCommitError(
                            "projection-precondition-changed",
                            "projection target appeared after stage",
                        )
                elif not self._projection_value_matches(
                    current,
                    projection.base,
                ):
                    raise FencedCommitError(
                        "projection-precondition-changed",
                        "projection target changed after stage",
                    )
        state_ref, manifest_effects = self._manifest_records(
            prepared.staged.manifest_bytes
        )
        if (
            state_ref.digest != _sha256(prepared.state_bytes)
            or state_ref.size != len(prepared.state_bytes)
        ):
            raise FencedCommitError(
                "precondition-mismatch", "manifest state differs from prepared bytes"
            )
        expected_effects = tuple(
            EffectRef(
                binding.blob_id,
                binding.digest,
                binding.kind,
                "objects/" + binding.digest.removeprefix("sha256:") + ".blob",
                binding.relative_path,
                binding.size,
            )
            for binding in prepared.effects
        )
        if manifest_effects != expected_effects:
            raise FencedCommitError(
                "precondition-mismatch", "manifest effects differ from prepared effects"
            )

    def _validate_lease_at(self, prepared: PreparedCommit, now: datetime) -> None:
        pending = prepared.admitted.pending_lease
        base = pending.base
        if (
            pending.action == "renewed"
            and pending.base_was_live
            and isinstance(base, FencedLease)
            and now >= _parse_time(base.lease_expires_at)
        ):
            raise FencedCommitError(
                "lease-precondition-changed", "base lease expired after staging"
            )
        if now >= _parse_time(pending.target.lease_expires_at):
            raise FencedCommitError(
                "lease-precondition-changed", "pending target lease expired"
            )

    def _precondition_checks(
        self,
        prepared: PreparedCommit,
        precondition: CommitPrecondition,
        commit_now: datetime,
    ) -> Optional[CommitResult]:
        self._validate_prepared_binding(prepared)
        if precondition != prepared.precondition:
            raise FencedCommitError("precondition-mismatch", "commit precondition is not stage-bound")
        if precondition.pending_lease_digest != prepared.admitted.pending_lease.digest:
            raise FencedCommitError("precondition-mismatch", "pending lease digest differs")
        self._reject_open_prepare()
        existing = self._lookup_operation(prepared.admitted.request)
        if existing is not None:
            return existing
        current, _digest_value = self._current_cas(prepared)
        base_lease: Union[LegacyAbsentLease, FencedLease]
        if current is None:
            base_lease = LegacyAbsentLease()
        else:
            base_lease = current.state.lease
        if base_lease != prepared.admitted.pending_lease.base:
            raise FencedCommitError("lease-precondition-changed", "base lease changed")
        admitted_at = _parse_time(prepared.admitted.pending_lease.admitted_at)
        recomputed = admit_lease(
            prepared.admitted.request,
            base_lease,
            admitted_at,
            self.lease_ttl_seconds,
            generated_lease_id=(
                prepared.admitted.pending_lease.target.lease_id
                if prepared.admitted.request.presented_lease_id is None
                else None
            ),
        )
        if recomputed != prepared.admitted.pending_lease:
            raise FencedCommitError("lease-precondition-changed", "pending lease decision changed")
        self._validate_lease_at(prepared, commit_now)
        if prepared.target_state.lease != recomputed.target:
            raise FencedCommitError("pending-lease-mismatch", "target state lease changed")
        try:
            validate_staged_generation(self.root, prepared.staged)
        except Exception as exc:
            raise FencedCommitError(getattr(exc, "code", "stage-invalid"), "staged generation changed") from exc
        return None

    def _prepare_document(
        self,
        prepared: PreparedCommit,
        generation_ref: RecordRef,
        state_ref: RecordRef,
        effects: tuple[EffectRef, ...],
        prepared_at: str,
    ) -> dict:
        return {
            "audit": _audit_document(_audit_record(prepared.admitted.request.audit)),
            "base": _base_document(
                BaseRef(prepared.precondition.base_generation, prepared.precondition.base_head_digest)
            ),
            "effects": [_effect_document(effect) for effect in effects],
            "fencing_epoch": prepared.admitted.pending_lease.target.fencing_epoch,
            "generation": _ref_document(generation_ref),
            "intent_digest": prepared.admitted.request.intent_digest,
            "operation_id": prepared.admitted.request.operation_id,
            "prepared_at": prepared_at,
            "projections": [
                _projection_document(projection)
                for projection in prepared.projections
            ],
            "schema": "mission-prepare/1",
            "session_id": prepared.admitted.request.session_id,
            "state": _ref_document(state_ref),
            "target_generation": prepared.admitted.target_generation,
            "transaction_id": prepared.transaction_id,
        }

    def _require_projection_ref(
        self,
        path: Path,
        reference: ProjectionFileRef,
        *,
        allowed_link_counts: tuple[int, ...],
    ) -> bytes:
        value = self._read_projection_bytes(
            path,
            limit=STATE_LIMIT,
            allowed_link_counts=allowed_link_counts,
        )
        assert value is not None
        content, identity = value
        if (
            identity != reference.identity
            or len(content) != reference.size
            or _sha256(content) != reference.digest
        ):
            raise FencedCommitError(
                "projection-invalid", "projection identity or bytes differ"
            )
        return content

    @staticmethod
    def _fsync_projection_directory(path: Path) -> None:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _apply_projections(self, prepared: PreparedCommit) -> None:
        if not prepared.projections:
            return
        bundle = (
            self.root
            / "transactions"
            / "projections"
            / prepared.transaction_id
        )
        for index, projection in enumerate(prepared.projections):
            after_path = bundle / projection.after.name
            self._require_projection_ref(
                after_path,
                projection.after,
                allowed_link_counts=(1,),
            )
            base_path = bundle / ("base-%03d.blob" % index)
            with self._pinned_projection_target(projection) as pinned:
                current = self._read_pinned_projection_bytes(
                    pinned,
                    limit=STATE_LIMIT,
                    allow_missing=True,
                    allowed_link_counts=(1,),
                )
                if projection.base is None:
                    if current is not None:
                        raise FencedCommitError(
                            "projection-precondition-changed",
                            "new projection target appeared",
                        )
                elif not self._projection_value_matches(
                    current,
                    projection.base,
                ):
                    raise FencedCommitError(
                        "projection-precondition-changed",
                        "projection base changed before publication",
                    )
                elif base_path.exists() or base_path.is_symlink():
                    raise FencedCommitError(
                        "projection-invalid",
                        "projection base backup already exists",
                    )
                else:
                    try:
                        os.rename(
                            pinned.target_name,
                            os.fspath(base_path),
                            src_dir_fd=pinned.descriptor,
                        )
                        self._verify_pinned_projection_target(pinned)
                    except OSError as exc:
                        raise FencedCommitError(
                            "projection-write-failed",
                            "projection base cannot be moved privately",
                        ) from exc
                    os.fsync(pinned.descriptor)
                    self._fsync_projection_directory(bundle)
                    self._require_projection_ref(
                        base_path,
                        projection.base,
                        allowed_link_counts=(1,),
                    )
                try:
                    os.link(
                        os.fspath(after_path),
                        pinned.target_name,
                        dst_dir_fd=pinned.descriptor,
                        follow_symlinks=False,
                    )
                    self._verify_pinned_projection_target(pinned)
                except OSError as exc:
                    raise FencedCommitError(
                        "projection-write-failed",
                        "projection target cannot be published",
                    ) from exc
                os.fsync(pinned.descriptor)
                self._fsync_projection_directory(bundle)
                published = self._read_pinned_projection_bytes(
                    pinned,
                    limit=STATE_LIMIT,
                    allowed_link_counts=(2,),
                )
                if not self._projection_value_matches(
                    published,
                    projection.after,
                ):
                    raise FencedCommitError(
                        "projection-write-failed",
                        "published projection identity differs",
                    )
            self._require_projection_ref(
                after_path,
                projection.after,
                allowed_link_counts=(2,),
            )
            self._fault("after-projection:%d" % index)

    def commit(self, prepared: PreparedCommit, precondition: CommitPrecondition) -> CommitResult:
        with self._lock():
            try:
                commit_now = _as_utc(self.clock())
                existing = self._precondition_checks(
                    prepared,
                    precondition,
                    commit_now,
                )
                generation_ref = RecordRef(
                    prepared.staged.generation_digest,
                    "generations/" + prepared.staged.generation_digest.removeprefix("sha256:") + ".json",
                    len(prepared.staged.manifest_bytes),
                )
                state_ref, effects = self._manifest_records(prepared.staged.manifest_bytes)
                now_text = _format_time(commit_now)
                prepare_document = self._prepare_document(
                    prepared, generation_ref, state_ref, effects, now_text
                )
                prepare_bytes = _canonical_bytes(prepare_document, limit=MAX_PREPARE_BYTES)
                prepare_path = self.root / "transactions" / "prepared" / (prepared.transaction_id + ".json")
            except BaseException as exc:
                self._discard_on_reject(prepared, cause=exc)
                raise
            if existing is not None:
                self._discard_on_reject(prepared)
                return existing
            if not self._invalidate_stage_binding(prepared):
                raise FencedCommitError(
                    "precondition-mismatch",
                    "private stage authority was already consumed",
                )
            self._publish_named_immutable(
                prepare_path,
                prepare_bytes,
                limit=MAX_PREPARE_BYTES,
                collision_code="immutable-prepare-collision",
            )
            self._fault("after-prepare")
            published: PublishedGeneration = publish_generation(self.root, prepared.staged)
            if published.generation_digest != generation_ref.digest:
                raise FencedCommitError("lineage-mismatch", "published generation digest differs")
            self._fault("after-generation-publish")
            self._apply_projections(prepared)
            commit = CommitRecord(
                audit=_audit_record(prepared.admitted.request.audit),
                base=BaseRef(prepared.precondition.base_generation, prepared.precondition.base_head_digest),
                committed_at=now_text,
                effects=effects,
                fencing_epoch=prepared.admitted.pending_lease.target.fencing_epoch,
                generation=generation_ref,
                intent_digest=prepared.admitted.request.intent_digest,
                operation_id=prepared.admitted.request.operation_id,
                session_id=prepared.admitted.request.session_id,
                state=state_ref,
                target_generation=prepared.admitted.target_generation,
                transaction_id=prepared.transaction_id,
            )
            commit_bytes = _canonical_bytes(_commit_document(commit), limit=MAX_COMMIT_BYTES)
            commit_digest = _sha256(commit_bytes)
            commit_ref = RecordRef(
                commit_digest,
                "commits/" + commit_digest.removeprefix("sha256:") + ".json",
                len(commit_bytes),
            )
            self._publish_named_immutable(
                self.root / commit_ref.path,
                commit_bytes,
                limit=MAX_COMMIT_BYTES,
                collision_code="immutable-commit-collision",
            )
            self._fault("after-commit-publish")
            head = HeadRecord(
                commit=commit_ref,
                generation=prepared.admitted.target_generation,
                session_id=prepared.admitted.request.session_id,
                state_generation=generation_ref,
            )
            head_bytes = _canonical_bytes(_head_document(head), limit=MAX_HEAD_BYTES)

            def final_authority_gate() -> None:
                self._fault("before-head-replace")
                self._current_cas(prepared)
                authority_now = _as_utc(self.clock())
                self._validate_lease_at(prepared, authority_now)

            self._replace_head(
                prepared.admitted.request.session_id,
                head_bytes,
                before_replace=final_authority_gate,
            )
            self._fault("after-head-replace")
            result = CommitResult(
                commit_digest=commit_digest,
                generation=head.generation,
                head_digest=_sha256(head_bytes),
                state_generation_digest=generation_ref.digest,
            )
            operation_document = {
                "commit_digest": commit_digest,
                "intent_digest": prepared.admitted.request.intent_digest,
                "operation_id": prepared.admitted.request.operation_id,
                "result": _result_document(result),
                "schema": "mission-operation/1",
                "session_id": prepared.admitted.request.session_id,
            }
            operation_bytes = _canonical_bytes(operation_document, limit=MAX_OPERATION_BYTES)
            self._publish_named_immutable(
                self._operation_path(
                    prepared.admitted.request.session_id,
                    prepared.admitted.request.operation_id,
                ),
                operation_bytes,
                limit=MAX_OPERATION_BYTES,
                collision_code="operation-intent-collision",
            )
            self._fault("after-operation-publish")
            snapshot = self._read_snapshot_unlocked(prepared.admitted.request.session_id)
            if snapshot.result != result:
                raise FencedCommitError("lineage-mismatch", "committed result cannot be verified")
            self._fault("after-lineage-verify")
            prepare_record = _parse_prepare(
                prepare_bytes,
                prepared.transaction_id + ".json",
            )
            report = self._recovery_report_unlocked(
                prepared.admitted.request.session_id
            )
            self._publish_resolution_unlocked(
                prepare_record,
                "finalized",
                report,
            )
            self._fault("after-resolution-marker")
            self._cleanup_projection_bundle_unlocked(
                prepare_record,
                target_is_authoritative=True,
            )
            self._remove_exact(prepare_path, prepare_bytes, limit=MAX_PREPARE_BYTES)
            self._fault("after-finalize")
            return result
