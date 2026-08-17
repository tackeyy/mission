"""Version-aware, fail-closed reader for authoritative session state."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from mission_kernel import MissionState, decode_mission_state, project_legacy_document
from mission_kernel.json_codec import (
    _reject_duplicate_json_pairs,
    decode_json_object,
    encode_json_value,
    freeze_json_value,
    thaw_json_object,
)
from mission_kernel.model import FencedLease, FrozenJsonObject, FrozenJsonValue, SchemaOrigin
from mission_kernel.versions import read_schema_version
from mission_common import (
    EVIDENCE_COMPLETION_ROLES,
    PASS_RATE_HEALTH_CLASSES,
    SESSION_ROLES,
    TERMINAL_OUTCOMES,
    classify_state,
    derive_terminal_outcome,
    has_scoring_checkpoint,
    parse_iso_datetime,
    session_role,
)

from .fenced_commit import LocalFencedRepository
from .repository_binding import (
    RepositoryFormat,
    RepositorySelectionError,
    inspect_repository_bytes,
)
from .strict_reader import STATE_LIMIT, read_stable_bytes


@dataclass(frozen=True)
class AuthoritativeLease:
    owner_session_id: str
    lease_id: str
    fencing_epoch: int
    lease_expires_at: str


@dataclass(frozen=True)
class AuthoritativeSnapshot:
    """Immutable projection of the fields interpreted by read-only consumers."""

    schema_origin: SchemaOrigin
    loop_active: bool
    passes: bool
    halt_reason: str
    halt_category: str
    phase: str
    iteration: int
    session_id: Optional[str]
    issue_ref: Optional[str]
    lease: Optional[AuthoritativeLease]
    owner_session_id: Optional[str]
    lease_id: Optional[str]
    fencing_epoch: Optional[int]
    lease_expires_at: Optional[str]
    pid: Optional[int]
    updated_at: Optional[str]
    heartbeat_at: Optional[str]
    last_progress_at: Optional[str]
    last_activity_at: Optional[str]
    score_history: Tuple[FrozenJsonValue, ...]
    mission: str
    threshold: float
    awaiting_user: bool
    project_root: Optional[str]
    logical_group_id: Optional[str]
    classification: str
    terminal_outcome: Optional[str]
    session_role: str
    has_scoring_checkpoint: bool
    progress_timestamp_field: Optional[str]
    progress_timestamp: Optional[str]
    progress_timestamp_valid: bool
    document: Union[FrozenJsonObject, dict[str, Any]]
    consumer_document: Union[FrozenJsonObject, dict[str, Any]]
    state_bytes: bytes
    generation: Optional[int] = None
    commit_digest: Optional[str] = None

    def document_copy(self) -> dict[str, Any]:
        """Return a detached copy for fields outside the authoritative projection."""

        if isinstance(self.consumer_document, FrozenJsonObject):
            return thaw_json_object(self.consumer_document)
        return copy.deepcopy(self.consumer_document)

    def raw_document_copy(self) -> dict[str, Any]:
        """Return the verified persisted document without compatibility projection."""

        if isinstance(self.document, FrozenJsonObject):
            return thaw_json_object(self.document)
        return copy.deepcopy(self.document)

    def matches_consumer_document(self, document: dict[str, Any]) -> bool:
        """Verify authoritative values in a sealed compatibility projection."""

        expected = self.document_copy()
        fields = (
            "loop_active", "passes", "halt_reason", "halt_category", "phase",
            "iteration", "session_id", "issue_ref", "owner_session_id",
            "lease_id", "fencing_epoch", "lease_expires_at", "pid",
            "updated_at", "heartbeat_at", "last_progress_at", "last_activity_at",
            "score_history", "mission", "threshold", "awaiting_user",
            "project_root", "logical_group_id", "session_role", "terminal_outcome",
        )
        return all(expected.get(field) == document.get(field) for field in fields)

    @property
    def last_score(self) -> Optional[float]:
        """Return a numeric composite only when the final history entry has one."""

        if not self.score_history:
            return None
        entry = self.score_history[-1]
        if isinstance(entry, FrozenJsonObject):
            value = entry.thaw().get("composite")
        elif isinstance(entry, dict):
            value = entry.get("composite")
        else:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None

    @property
    def artifact_terminal_outcome(self) -> Optional[str]:
        """Preserve the artifact coverage consumer's historical terminal rule."""

        values = self.raw_document_copy()
        outcome = values.get("terminal_outcome")
        if isinstance(outcome, str) and outcome:
            return outcome
        if values.get("passes") is True:
            return "completed_pass"
        if values.get("loop_active") is False:
            return "failed"
        return None

    def progress_age_seconds(
        self, now: Optional[datetime] = None
    ) -> Optional[float]:
        if not self.progress_timestamp_valid or self.progress_timestamp is None:
            return None
        updated = parse_iso_datetime(self.progress_timestamp)
        if updated is None:
            return None
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        base = now or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        seconds = (
            base.astimezone(timezone.utc) - updated.astimezone(timezone.utc)
        ).total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return seconds

    def pass_rate_health(
        self, *, now: Optional[datetime], stale_after_sec: int
    ) -> str:
        if self.classification != "incomplete":
            return self.classification
        age = self.progress_age_seconds(now)
        if age is None or age >= max(0, stale_after_sec):
            return "stale"
        return "active" if self.has_scoring_checkpoint else "active-no-score"

    def dedupe_rank(self, source_path: str = "") -> tuple[int, float, int, str]:
        """Prefer terminal success, then recency, then live/path order."""

        status_rank = {
            "pass": 0,
            "halt": 1,
            "incomplete": 2,
        }.get(self.classification, 3)
        updated = parse_iso_datetime(self.updated_at)
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        updated_rank = updated.timestamp() if updated is not None else 0.0
        if "/archive/worktree-" in source_path:
            path_rank = 1
        elif "/sessions/" in source_path:
            path_rank = 0
        else:
            path_rank = 2
        return (status_rank, -updated_rank, path_rank, source_path)


def _optional_string(document: dict[str, Any], name: str) -> Optional[str]:
    value = document.get(name)
    return value if isinstance(value, str) and value else None


def _bind_expected_session_id(
    snapshot: AuthoritativeSnapshot,
    expected_session_id: Optional[str],
) -> AuthoritativeSnapshot:
    if expected_session_id is None:
        return snapshot
    if snapshot.session_id is None and snapshot.schema_origin is not SchemaOrigin.V5:
        return snapshot
    if snapshot.session_id != expected_session_id:
        raise ValueError("session identity differs from selected session")
    return snapshot


def is_live_session_path(path: Path) -> bool:
    """Return whether path is an immediate `.mission-state/sessions` child."""

    candidate = Path(path)
    return (
        candidate.parent.name == "sessions"
        and candidate.parent.parent.name == ".mission-state"
    )


def expected_session_id_for_live_path(path: Path) -> Optional[str]:
    """Return the filename identity only for live `.mission-state/sessions`."""

    candidate = Path(path)
    return candidate.stem if is_live_session_path(candidate) else None


def _inspect_repository_bytes(
    source: bytes, *, expected_session_id: Optional[str]
):
    try:
        return inspect_repository_bytes(
            source, expected_session_id=expected_session_id
        )
    except RepositorySelectionError as exc:
        if exc.code == "repository-session-mismatch":
            raise ValueError(
                "session identity differs from selected session"
            ) from exc
        raise


def _decode_legacy_compatibility_bytes(source: bytes) -> dict[str, Any]:
    """Decode historical JSON without accepting duplicate keys.

    Legacy aggregate consumers historically accepted non-finite score values and
    then ignored them.  Keep that narrow compatibility here while retaining the
    stable-file and duplicate-key guards.
    """

    try:
        document = json.loads(
            source.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("authoritative session state is not UTF-8") from exc
    if not isinstance(document, dict):
        raise ValueError("authoritative session state must be an object")
    return document


def _legacy_compatibility_snapshot(
    document: dict[str, Any],
    *,
    state_bytes: bytes,
    expected_session_id: Optional[str],
    allow_missing_schema_session_mismatch: bool,
) -> AuthoritativeSnapshot:
    """Project a legacy document without upgrading old field tolerance.

    This seam is intentionally unavailable to live v5 verdicts.  It preserves the
    raw v1-v4 document for historical aggregate/classification behavior while
    normalizing only the typed convenience fields carried by the snapshot.
    """

    if "schema" in document or {"commit", "state_generation"} & set(document):
        raise ValueError("legacy compatibility input uses an unsupported format")
    schema_origin = read_schema_version(document, max_reader_version=4)
    identity_values = (document.get("mission"), document.get("mission_id"))
    has_identity = any(isinstance(value, str) and value for value in identity_values)
    has_control = (
        isinstance(document.get("phase"), str)
        or type(document.get("loop_active")) is bool
    )
    if not has_identity or not has_control:
        raise ValueError("legacy compatibility input lacks identity or control")
    embedded_session_id = document.get("session_id")
    if embedded_session_id is not None and (
        not isinstance(embedded_session_id, str) or not embedded_session_id
    ):
        raise ValueError("legacy session identity is invalid")
    if (
        expected_session_id is not None
        and embedded_session_id is not None
        and embedded_session_id != expected_session_id
        and not (
            allow_missing_schema_session_mismatch
            and schema_origin is SchemaOrigin.MISSING
        )
    ):
        raise ValueError("session identity differs from selected session")

    def optional_string(name: str) -> Optional[str]:
        value = document.get(name)
        return value if isinstance(value, str) and value else None

    loop_active = document.get("loop_active") is True
    passes = document.get("passes") is True
    awaiting_user = document.get("awaiting_user") is True
    iteration_value = document.get("iteration", 0)
    iteration = (
        iteration_value
        if type(iteration_value) is int and iteration_value >= 0
        else 0
    )
    pid_value = document.get("pid")
    pid = pid_value if type(pid_value) is int and pid_value >= 0 else None
    threshold_value = document.get("threshold", 4.0)
    threshold = (
        float(threshold_value)
        if (
            not isinstance(threshold_value, bool)
            and isinstance(threshold_value, (int, float))
            and math.isfinite(float(threshold_value))
        )
        else 4.0
    )
    history = document.get("score_history")
    score_history = tuple(copy.deepcopy(history)) if isinstance(history, list) else ()

    lease_values = tuple(document.get(name) for name in (
        "owner_session_id", "lease_id", "fencing_epoch", "lease_expires_at"
    ))
    if (
        isinstance(lease_values[0], str)
        and bool(lease_values[0])
        and isinstance(lease_values[1], str)
        and bool(lease_values[1])
        and type(lease_values[2]) is int
        and lease_values[2] >= 0
        and isinstance(lease_values[3], str)
        and bool(lease_values[3])
    ):
        lease = AuthoritativeLease(
            owner_session_id=lease_values[0],
            lease_id=lease_values[1],
            fencing_epoch=lease_values[2],
            lease_expires_at=lease_values[3],
        )
    else:
        lease = None

    progress_timestamp_field = None
    progress_timestamp = None
    progress_timestamp_valid = True
    for timestamp_field in (
        "heartbeat_at", "last_progress_at", "last_activity_at", "updated_at"
    ):
        timestamp_value = document.get(timestamp_field)
        if not timestamp_value:
            continue
        if not isinstance(timestamp_value, str):
            progress_timestamp_valid = False
            break
        progress_timestamp_field = timestamp_field
        progress_timestamp = timestamp_value
        break

    raw_document = copy.deepcopy(document)
    return AuthoritativeSnapshot(
        schema_origin=schema_origin,
        loop_active=loop_active,
        passes=passes,
        halt_reason=(
            document.get("halt_reason")
            if isinstance(document.get("halt_reason"), str)
            else ""
        ),
        halt_category=document.get("halt_category", ""),
        phase=(
            document.get("phase")
            if isinstance(document.get("phase"), str)
            else "unknown"
        ),
        iteration=iteration,
        session_id=embedded_session_id,
        issue_ref=optional_string("issue_ref"),
        lease=lease,
        owner_session_id=optional_string("owner_session_id"),
        lease_id=optional_string("lease_id"),
        fencing_epoch=(
            lease_values[2]
            if type(lease_values[2]) is int and lease_values[2] >= 0
            else None
        ),
        lease_expires_at=optional_string("lease_expires_at"),
        pid=pid,
        updated_at=optional_string("updated_at"),
        heartbeat_at=optional_string("heartbeat_at"),
        last_progress_at=optional_string("last_progress_at"),
        last_activity_at=optional_string("last_activity_at"),
        score_history=score_history,
        mission=(
            document.get("mission")
            if isinstance(document.get("mission"), str)
            else ""
        ),
        threshold=threshold,
        awaiting_user=awaiting_user,
        project_root=optional_string("project_root"),
        logical_group_id=optional_string("logical_group_id"),
        classification=classify_state(document),
        terminal_outcome=derive_terminal_outcome(document),
        session_role=session_role(document),
        has_scoring_checkpoint=has_scoring_checkpoint(document),
        progress_timestamp_field=progress_timestamp_field,
        progress_timestamp=progress_timestamp,
        progress_timestamp_valid=progress_timestamp_valid,
        document=raw_document,
        consumer_document=raw_document,
        state_bytes=state_bytes,
    )


def read_legacy_compatibility_snapshot(
    session_path: Union[Path, str],
    *,
    expected_session_id: Optional[str] = None,
    allow_missing_schema_session_mismatch: bool = False,
) -> AuthoritativeSnapshot:
    """Read a legacy aggregate-compatible document through stable file bytes."""

    source = read_stable_bytes(Path(session_path), limit=STATE_LIMIT)
    document = _decode_legacy_compatibility_bytes(source)
    return _legacy_compatibility_snapshot(
        document,
        state_bytes=source,
        expected_session_id=expected_session_id,
        allow_missing_schema_session_mismatch=allow_missing_schema_session_mismatch,
    )


def legacy_compatibility_snapshot_from_document(
    document: dict[str, Any],
    *,
    expected_session_id: Optional[str] = None,
    allow_missing_schema_session_mismatch: bool = False,
) -> AuthoritativeSnapshot:
    """Rehydrate an already verified legacy aggregate snapshot document."""

    source = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _legacy_compatibility_snapshot(
        copy.deepcopy(document),
        state_bytes=source,
        expected_session_id=expected_session_id,
        allow_missing_schema_session_mismatch=allow_missing_schema_session_mismatch,
    )


def _snapshot_from_document(
    document: FrozenJsonObject,
    *,
    schema_origin: SchemaOrigin,
    state_bytes: bytes,
    generation: Optional[int] = None,
    commit_digest: Optional[str] = None,
    project_root: Optional[str] = None,
    raw_document: Optional[FrozenJsonObject] = None,
) -> AuthoritativeSnapshot:
    values = thaw_json_object(document)
    loop_active = values.get("loop_active", False)
    passes = values.get("passes", False)
    awaiting_user = values.get("awaiting_user", False)
    iteration = values.get("iteration", 0)
    pid = values.get("pid")
    threshold = values.get("threshold", 4.0)
    score_history = values.get("score_history", [])
    for name, value in (
        ("loop_active", loop_active),
        ("passes", passes),
        ("awaiting_user", awaiting_user),
    ):
        if type(value) is not bool:
            raise ValueError("authoritative field %s must be boolean" % name)
    if type(iteration) is not int or iteration < 0:
        raise ValueError("authoritative field iteration must be a non-negative integer")
    if pid is not None and (type(pid) is not int or pid < 0):
        raise ValueError("authoritative field pid must be a non-negative integer or null")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("authoritative field threshold must be numeric")
    if not isinstance(score_history, list):
        raise ValueError("authoritative field score_history must be an array")

    lease_values = tuple(values.get(name) for name in (
        "owner_session_id", "lease_id", "fencing_epoch", "lease_expires_at"
    ))
    owner_session_id = _optional_string(values, "owner_session_id")
    lease_id = _optional_string(values, "lease_id")
    fencing_epoch_value = values.get("fencing_epoch")
    fencing_epoch = (
        fencing_epoch_value
        if type(fencing_epoch_value) is int and fencing_epoch_value >= 0
        else None
    )

    lease_expires_at = _optional_string(values, "lease_expires_at")
    if all(value is None for value in lease_values):
        lease = None
    elif (
        isinstance(lease_values[0], str)
        and bool(lease_values[0])
        and isinstance(lease_values[1], str)
        and bool(lease_values[1])
        and type(lease_values[2]) is int
        and lease_values[2] >= 0
        and isinstance(lease_values[3], str)
        and bool(lease_values[3])
    ):
        lease = AuthoritativeLease(
            owner_session_id=lease_values[0],
            lease_id=lease_values[1],
            fencing_epoch=lease_values[2],
            lease_expires_at=lease_values[3],
        )
    elif schema_origin is SchemaOrigin.V5:
        raise ValueError("authoritative lease fields must be absent or complete")
    else:
        # Pre-v5 writers historically emitted individual lease diagnostics.
        # They do not become a fenced lease until the complete tuple exists.
        lease = None

    halt_reason = values.get("halt_reason", "")
    halt_category = values.get("halt_category", "")
    phase = values.get("phase", "unknown")
    mission = values.get("mission", "")
    for name, value in (
        ("halt_reason", halt_reason),
        ("halt_category", halt_category),
        ("phase", phase),
        ("mission", mission),
    ):
        if not isinstance(value, str):
            raise ValueError("authoritative field %s must be a string" % name)

    frozen_score_history = next(
        value for key, value in document.items if key == "score_history"
    ) if "score_history" in values else ()
    if not isinstance(frozen_score_history, tuple):
        raise ValueError("authoritative field score_history must be an array")
    progress_timestamp_field = None
    progress_timestamp = None
    progress_timestamp_valid = True
    for timestamp_field in (
        "heartbeat_at", "last_progress_at", "last_activity_at", "updated_at"
    ):
        timestamp_value = values.get(timestamp_field)
        if not timestamp_value:
            continue
        if not isinstance(timestamp_value, str):
            progress_timestamp_valid = False
            break
        progress_timestamp_field = timestamp_field
        progress_timestamp = timestamp_value
        break
    return AuthoritativeSnapshot(
        schema_origin=schema_origin,
        loop_active=loop_active,
        passes=passes,
        halt_reason=halt_reason,
        halt_category=halt_category,
        phase=phase,
        iteration=iteration,
        session_id=_optional_string(values, "session_id"),
        issue_ref=_optional_string(values, "issue_ref"),
        lease=lease,
        owner_session_id=owner_session_id,
        lease_id=lease_id,
        fencing_epoch=fencing_epoch,
        lease_expires_at=lease_expires_at,
        pid=pid,
        updated_at=_optional_string(values, "updated_at"),
        heartbeat_at=_optional_string(values, "heartbeat_at"),
        last_progress_at=_optional_string(values, "last_progress_at"),
        last_activity_at=_optional_string(values, "last_activity_at"),
        score_history=frozen_score_history,
        mission=mission,
        threshold=float(threshold),
        awaiting_user=awaiting_user,
        project_root=project_root or _optional_string(values, "project_root"),
        logical_group_id=_optional_string(values, "logical_group_id"),
        classification=classify_state(values),
        terminal_outcome=derive_terminal_outcome(values),
        session_role=session_role(values),
        has_scoring_checkpoint=has_scoring_checkpoint(values),
        progress_timestamp_field=progress_timestamp_field,
        progress_timestamp=progress_timestamp,
        progress_timestamp_valid=progress_timestamp_valid,
        document=raw_document or document,
        consumer_document=document,
        state_bytes=state_bytes,
        generation=generation,
        commit_digest=commit_digest,
    )


def _canonical_v5_consumer_document(
    state: MissionState, raw_document: FrozenJsonObject
) -> FrozenJsonObject:
    values = thaw_json_object(raw_document)
    routing = values["guidance"]["routing"]
    seed = {
        "mission": "",
        "mission_id": "",
        "session_id": "",
        "phase": "planning",
        "terminal_outcome": None,
        "iteration": 0,
        "max_iter": None,
        "threshold": 4.0,
        "reviewer_count": 1,
        "stagnation_count": 0,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "halt_category": None,
        "session_role": "implementer",
        "review_evidence_refs": [],
        "score_history": [],
        "awaiting_user": routing["awaiting_user"],
        "issue_ref": routing["issue_ref"],
        "complexity": routing["complexity"],
    }
    frozen_seed = freeze_json_value(seed)
    if not isinstance(frozen_seed, FrozenJsonObject):
        raise ValueError("canonical v5 compatibility seed must be an object")
    projected_state = replace(state, legacy_passthrough=frozen_seed)
    projected = decode_json_object(project_legacy_document(projected_state))
    if not isinstance(projected, FrozenJsonObject):
        raise ValueError("canonical v5 consumer projection must be an object")
    return projected


def _snapshot_from_k1_state(
    state: MissionState,
    raw_document: FrozenJsonObject,
    *,
    state_bytes: bytes,
    generation: Optional[int],
    commit_digest: Optional[str],
    project_root: Optional[str],
) -> AuthoritativeSnapshot:
    if state.schema_origin is not SchemaOrigin.V5:
        projected = decode_json_object(project_legacy_document(state))
        return _snapshot_from_document(
            projected,
            schema_origin=SchemaOrigin.V5,
            state_bytes=state_bytes,
            generation=generation,
            commit_digest=commit_digest,
            project_root=project_root,
            raw_document=raw_document,
        )

    consumer_document = _canonical_v5_consumer_document(state, raw_document)
    values = thaw_json_object(consumer_document)
    control = state.control
    lease = state.lease
    assert isinstance(lease, FencedLease)
    frozen_scores = tuple(score.payload for score in state.scores)
    terminal_outcome = (
        control.terminal_outcome.value
        if control.terminal_outcome is not None else None
    )
    if terminal_outcome is not None:
        classification = "pass" if terminal_outcome == "completed_pass" else "halt"
    elif control.passes:
        classification = "pass"
    elif control.halt_reason:
        classification = "halt"
    elif not control.loop_active:
        classification = "abandoned"
    else:
        classification = "incomplete"
    has_checkpoint = any(
        isinstance(score.payload.thaw().get("composite"), (int, float))
        and not isinstance(score.payload.thaw().get("composite"), bool)
        and math.isfinite(float(score.payload.thaw()["composite"]))
        for score in state.scores
    )
    return AuthoritativeSnapshot(
        schema_origin=SchemaOrigin.V5,
        loop_active=control.loop_active,
        passes=control.passes,
        halt_reason=control.halt_reason,
        halt_category=(
            control.halt_category.value if control.halt_category is not None else ""
        ),
        phase=control.phase.value,
        iteration=control.iteration,
        session_id=state.identity.session_id,
        issue_ref=_optional_string(values, "issue_ref"),
        lease=AuthoritativeLease(
            owner_session_id=lease.owner_session_id,
            lease_id=lease.lease_id,
            fencing_epoch=lease.fencing_epoch,
            lease_expires_at=lease.lease_expires_at,
        ),
        owner_session_id=lease.owner_session_id,
        lease_id=lease.lease_id,
        fencing_epoch=lease.fencing_epoch,
        lease_expires_at=lease.lease_expires_at,
        pid=None,
        updated_at=None,
        heartbeat_at=None,
        last_progress_at=None,
        last_activity_at=None,
        score_history=frozen_scores,
        mission=state.identity.mission or "",
        threshold=control.threshold if control.threshold is not None else 4.0,
        awaiting_user=values["awaiting_user"],
        project_root=project_root,
        logical_group_id=None,
        classification=classification,
        terminal_outcome=terminal_outcome,
        session_role=control.session_role.value,
        has_scoring_checkpoint=has_checkpoint,
        progress_timestamp_field=None,
        progress_timestamp=None,
        progress_timestamp_valid=True,
        document=raw_document,
        consumer_document=consumer_document,
        state_bytes=state_bytes,
        generation=generation,
        commit_digest=commit_digest,
    )


def authoritative_snapshot_from_document(
    document: dict[str, Any],
    *,
    expected_session_id: Optional[str] = None,
) -> AuthoritativeSnapshot:
    """Project an already-sealed legacy document through the same typed reader."""

    frozen = freeze_json_value(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise ValueError("authoritative session state must be an object")
    state_bytes = encode_json_value(frozen)
    values = thaw_json_object(frozen)
    if "schema" in values or {"commit", "state_generation"} & set(values):
        raise ValueError("sealed state document uses an unsupported format")
    schema_origin = read_schema_version(values, max_reader_version=5)
    if schema_origin is SchemaOrigin.V5:
        state = decode_mission_state(state_bytes)
        snapshot = _snapshot_from_k1_state(
            state,
            frozen,
            state_bytes=state_bytes,
            generation=None,
            commit_digest=None,
            project_root=None,
        )
    else:
        snapshot = _snapshot_from_document(
            frozen, schema_origin=schema_origin, state_bytes=state_bytes
        )
    return _bind_expected_session_id(snapshot, expected_session_id)


def authoritative_snapshot_from_validated_archive_bytes(
    state_bytes: bytes,
    *,
    expected_session_id: Optional[str] = None,
) -> AuthoritativeSnapshot:
    """Strictly project manifest-validated archive state bytes.

    Archive generations contain state documents, not mutable live repository
    heads.  Their caller has already bound the bytes to the archive manifest, so
    this path must not run live-head format selection.
    """

    frozen = decode_json_object(state_bytes)
    values = thaw_json_object(frozen)
    if "schema" in values or "commit" in values:
        raise ValueError("validated archive state uses an unsupported format")
    schema_origin = read_schema_version(values, max_reader_version=5)
    if schema_origin is SchemaOrigin.V5:
        state = decode_mission_state(state_bytes)
        snapshot = _snapshot_from_k1_state(
            state,
            frozen,
            state_bytes=state_bytes,
            generation=None,
            commit_digest=None,
            project_root=None,
        )
        return _bind_expected_session_id(snapshot, expected_session_id)
    snapshot = _snapshot_from_document(
        frozen, schema_origin=schema_origin, state_bytes=state_bytes
    )
    return _bind_expected_session_id(snapshot, expected_session_id)


def authoritative_snapshot_from_bytes(
    state_bytes: bytes,
    *,
    expected_session_id: Optional[str] = None,
) -> AuthoritativeSnapshot:
    """Backward-compatible name for the validated archive bytes seam."""

    return authoritative_snapshot_from_validated_archive_bytes(
        state_bytes, expected_session_id=expected_session_id
    )


def summarize_authoritative_pass_rate_population(
    snapshots: list[AuthoritativeSnapshot],
    *,
    now: Optional[datetime] = None,
    stale_after_sec: int,
) -> dict[str, Any]:
    """Return the existing pass-rate contract from typed snapshots only."""

    observation_now = now or datetime.now(timezone.utc)
    health_classes = [
        snapshot.pass_rate_health(
            now=observation_now, stale_after_sec=stale_after_sec
        )
        for snapshot in snapshots
    ]
    counts = {name: 0 for name in PASS_RATE_HEALTH_CLASSES}
    for classification in health_classes:
        counts[classification] += 1
    terminal_outcomes = [snapshot.terminal_outcome for snapshot in snapshots]
    terminal_outcome_counts = {name: 0 for name in TERMINAL_OUTCOMES}
    for outcome in terminal_outcomes:
        if outcome is not None:
            terminal_outcome_counts[outcome] += 1
    terminal_count = sum(terminal_outcome_counts.values())
    routed = terminal_outcome_counts["routed_elsewhere"]
    completed_denominator = sum(
        counts[name] for name in ("pass", "halt", "abandoned", "stale")
    ) - routed
    pass_count = counts["pass"]
    role_counts = {name: 0 for name in SESSION_ROLES}
    implementer_passes = 0
    implementer_completed = 0
    evidence_completed = 0
    evidence_comparable = 0
    for snapshot, outcome in zip(snapshots, terminal_outcomes):
        role_counts[snapshot.session_role] += 1
        if snapshot.session_role == "implementer" and outcome in {
            "completed_pass", "failed", "incomplete"
        }:
            implementer_completed += 1
            if outcome == "completed_pass":
                implementer_passes += 1
        if snapshot.session_role in EVIDENCE_COMPLETION_ROLES and outcome in {
            "completed_evidence", "failed", "incomplete"
        }:
            evidence_comparable += 1
            if outcome == "completed_evidence":
                evidence_completed += 1
    raw_denominator = len(snapshots)
    return {
        "health_classes": health_classes,
        "terminal_outcomes": terminal_outcomes,
        "terminal_outcome_counts": terminal_outcome_counts,
        "terminal_count": terminal_count,
        "non_terminal_count": len(snapshots) - terminal_count,
        "routed_count": routed,
        "role_counts": role_counts,
        "implementer_pass_rate_numerator": implementer_passes,
        "implementer_pass_rate_denominator": implementer_completed,
        "implementer_pass_rate": (
            implementer_passes / implementer_completed
            if implementer_completed else None
        ),
        "evidence_completion_rate_numerator": evidence_completed,
        "evidence_completion_rate_denominator": evidence_comparable,
        "evidence_completion_rate": (
            evidence_completed / evidence_comparable
            if evidence_comparable else None
        ),
        "raw_pass_rate_numerator": pass_count,
        "raw_pass_rate_denominator": raw_denominator,
        "raw_pass_rate": pass_count / raw_denominator if raw_denominator else None,
        "completed_pass_rate_numerator": pass_count,
        "completed_pass_rate_denominator": completed_denominator,
        "completed_pass_rate": (
            pass_count / completed_denominator if completed_denominator else None
        ),
        "active_count": counts["active"],
        "active_no_score_count": counts["active-no-score"],
        "stale_count": counts["stale"],
        "halt_count": counts["halt"],
        "abandoned_count": counts["abandoned"],
        "incomplete_count": (
            counts["active"] + counts["active-no-score"] + counts["stale"]
        ),
    }


def read_authoritative_snapshot(
    session_path: Union[Path, str],
    *,
    expected_session_id: Optional[str] = None,
) -> AuthoritativeSnapshot:
    """Read a legacy document or resolve a v5 head through verified lineage."""

    path = Path(session_path)
    source = read_stable_bytes(path, limit=STATE_LIMIT)
    inspected = _inspect_repository_bytes(
        source, expected_session_id=expected_session_id
    )
    if inspected.format is RepositoryFormat.V5:
        values = thaw_json_object(inspected.document)
        selected_session_id = expected_session_id
        if selected_session_id is None:
            selected_session_id = _optional_string(values, "session_id")
        if selected_session_id is None:
            raise ValueError("v5 head has no session identity")
        repository = LocalFencedRepository(path.parent.parent)
        repository_snapshot = repository.read(selected_session_id)
        state_document = decode_json_object(repository_snapshot.state_bytes)
        snapshot = _snapshot_from_k1_state(
            repository_snapshot.state,
            state_document,
            state_bytes=repository_snapshot.state_bytes,
            generation=repository_snapshot.head.generation,
            commit_digest=repository_snapshot.head.commit.digest,
            project_root=str(path.parent.parent.parent),
        )
        return _bind_expected_session_id(snapshot, expected_session_id)

    document = inspected.document
    values = thaw_json_object(document)
    schema_origin = read_schema_version(values, max_reader_version=4)
    snapshot = _snapshot_from_document(
        document, schema_origin=schema_origin, state_bytes=source
    )
    return _bind_expected_session_id(snapshot, expected_session_id)
