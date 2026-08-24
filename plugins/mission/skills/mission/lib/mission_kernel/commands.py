"""Closed K2 command subset whose authority is present in MissionState."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Optional, Union

from .json_codec import decode_json_object, encode_json_object, freeze_json_value
from .model import FrozenJsonObject
from .model import HaltCategory, Phase, PreparedHandoff
from .artifact import ArtifactEffectClaim
from .a4 import SpecialistRecommendationProjection


@dataclass(frozen=True)
class CompatibilityPayload:
    """Deeply immutable legacy observations accepted by one kernel command."""

    upserts: FrozenJsonObject = FrozenJsonObject(())
    removals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        upserts = self.upserts
        if not isinstance(upserts, FrozenJsonObject):
            upserts = freeze_json_value(upserts)
        if not isinstance(upserts, FrozenJsonObject):
            raise TypeError("compatibility-upserts-invalid")
        removals = self.removals
        if type(removals) is not tuple:
            removals = tuple(removals)
        object.__setattr__(self, "upserts", upserts)
        object.__setattr__(self, "removals", removals)


EMPTY_COMPATIBILITY_PAYLOAD = CompatibilityPayload()


@dataclass(frozen=True)
class AdvancePhase:
    target: Phase
    prepared_handoff: Optional[PreparedHandoff] = None
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD


@dataclass(frozen=True)
class MarkHalt:
    category: HaltCategory
    reason: str
    superseded: bool = False
    at: Optional[str] = None
    legacy_reason: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD
    extension_fields: FrozenJsonObject = FrozenJsonObject(())
    permission_observation: bool = False


@dataclass(frozen=True)
class Reactivate:
    expected_category: HaltCategory
    reason: str
    approved_by_user: bool
    target: Phase
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD


@dataclass(frozen=True)
class ResumeStale:
    target: Phase
    new_pid: Optional[int] = None
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD


@dataclass(frozen=True)
class MarkPass:
    """Request the kernel's sole completion transition.

    Evidence adapters and application use cases validate external bytes before
    constructing this command.  The kernel still owns the final conjunction of
    score, findings, artifact, specialist, and force-approval facts.
    """

    force: bool = False
    force_approval_verified: bool = False
    artifact_gate_satisfied: bool = False
    specialist_gate_satisfied: bool = False
    verified_score_index: Optional[int] = None
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD


@dataclass(frozen=True)
class SetExtensionFields:
    """Request generic extension-property writes outside dedicated authority.

    The closed field classification below is the kernel's authority: keys owned
    by a dedicated lifecycle, lease, progress, scoring, or evidence command are
    rejected by the reducer, so the generic command can never bypass the
    command that owns a state transition or its audit trail (#617 批1-a).
    """

    fields: FrozenJsonObject
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD


@dataclass(frozen=True)
class InitializeArtifact:
    at: str
    path: str
    format: str
    title: str
    redaction_status: str
    required_for_pass: bool
    effect: ArtifactEffectClaim


@dataclass(frozen=True)
class AppendArtifactBlock:
    at: str
    section: str
    content: str
    source: Optional[str]
    label: Optional[str]


@dataclass(frozen=True)
class RenderArtifact:
    at: str
    redaction_status: Optional[str]
    effect: ArtifactEffectClaim


@dataclass(frozen=True)
class ExportArtifact:
    at: str
    destination: str
    redaction_status: str
    artifact_effect: ArtifactEffectClaim
    export_effect: ArtifactEffectClaim


@dataclass(frozen=True)
class RecordArtifactPublication:
    at: str
    provider: str
    destination: Optional[str]
    approval_text: str
    confirmed: bool
    effect: ArtifactEffectClaim


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    ok: bool
    detail: Optional[str]


@dataclass(frozen=True)
class ProgressEffectClaim:
    kind: str
    target: str
    digest: str
    size: int


@dataclass(frozen=True)
class ContextManifestEffectClaim:
    kind: str
    target: str
    publication_path: str
    digest: str
    size: int


@dataclass(frozen=True)
class UpdateProgress:
    at: str
    total: int
    completed: int
    batch_size: Optional[int]
    last_unit: Optional[str]
    artifact_path: Optional[str]
    iteration: int
    effect: ProgressEffectClaim


@dataclass(frozen=True)
class ClearProgress:
    at: str


@dataclass(frozen=True)
class GenerateContextManifest:
    at: str
    iteration: int
    effect: ContextManifestEffectClaim


@dataclass(frozen=True)
class RecordVerification:
    at: str
    iteration: int
    checks: tuple[VerificationCheck, ...]


@dataclass(frozen=True)
class CanonicalPlanObservation:
    path: str
    digest: str
    generation: int
    source: str
    source_id: str
    selection_source: str
    iteration: int
    ordered_step_ids: tuple[str, ...]
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    raw: bytes


@dataclass(frozen=True)
class BeginExecutorHandoff:
    at: str
    plan: CanonicalPlanObservation


@dataclass(frozen=True)
class VerifyExecutorStep:
    at: str
    step_id: str
    plan: CanonicalPlanObservation


@dataclass(frozen=True)
class RecordExecutorStep:
    at: str
    step_id: str
    result: str
    plan: CanonicalPlanObservation


@dataclass(frozen=True)
class CompleteExecutorHandoff:
    at: str
    plan: CanonicalPlanObservation


class CanonicalPlanRejectionCode(str, Enum):
    PATH_INVALID = "canonical-plan-path-invalid"
    FILE_INVALID = "canonical-plan-file-invalid"
    DIGEST_DRIFT = "canonical-plan-digest-drift"
    JSON_INVALID = "canonical-plan-json-invalid"
    NOT_CANONICAL = "canonical-plan-not-canonical"
    SCHEMA_INVALID = "canonical-plan-schema-invalid"
    STEPS_INVALID = "canonical-plan-steps-invalid"
    STEP_IDS_INVALID = "canonical-plan-step-ids-invalid"
    GENERATION_MISMATCH = "canonical-plan-generation-mismatch"
    SOURCE_MISMATCH = "canonical-plan-source-mismatch"
    SOURCE_ID_MISMATCH = "canonical-plan-source_id-mismatch"
    SELECTION_SOURCE_MISMATCH = "canonical-plan-selection_source-mismatch"
    ITERATION_MISMATCH = "canonical-plan-iteration-mismatch"
    PROVIDER_IMPORT_MISSING = "canonical-plan-provider-import-missing"
    PROVIDER_CANDIDATE_MISMATCH = "canonical-plan-provider-candidate-mismatch"
    PROVIDER_INVOCATION_MISMATCH = "canonical-plan-provider-invocation-mismatch"
    PROVIDER_SOURCE_DIGEST_MISMATCH = "canonical-plan-provider-source-digest-mismatch"
    SOURCE_RECORD_MISSING = "canonical-plan-source-record-missing"


@dataclass(frozen=True)
class RejectExecutorHandoff:
    at: str
    attempted_operation: str
    reason_code: CanonicalPlanRejectionCode


@dataclass(frozen=True)
class RecordSpecialistRecommendation:
    at: str
    expected_complexity: Optional[str]
    expected_iteration: int
    projection: SpecialistRecommendationProjection


# Fields whose value is fixed at genesis or owned by the pass gate; a generic
# write would forge identity or completion evidence.  ``init`` recomputes
# mission identity, and pass-gate facts only move through their own commands.
GENERIC_SET_FROZEN_FIELDS = frozenset(
    {
        "mission",
        "mission_id",
        "passes",
        "passes_forced",
        "force_reason",
        "score_history",
        "failure_ledger",
        "threshold",
        "schema_version",
        "session_role",
        "terminal_outcome",
        "artifact_applicability",
        "artifact",
        "artifact_path",
        "artifact_lint",
        "artifact_lint_identity",
        "artifact_lint_status",
        "project_root",
        "started_at",
        "created_at_session",
        "reactivation_history",
    }
)


# Fields whose authority belongs to a dedicated lifecycle, lease, progress, or
# scoring command.  Generic ``set`` remains available for extension properties
# such as complexity and bounded orchestration observations, but cannot bypass
# the command that owns a state transition or its audit trail.
GENERIC_SET_DEDICATED_FIELDS = frozenset(
    {
        "phase",
        "phase_started_at",
        "phase_durations_sec",
        "activity_current",
        "activity_segments",
        "activity_rollup",
        "activity_last_event_at",
        "activity_last_event_phase",
        "activity_anomaly_counts",
        "activity_unobserved_gap_sec",
        "activity_unobserved_gap_reasons_sec",
        "pid",
        "pid_source",
        "loop_active",
        "halt_reason",
        "halt_category",
        "resume_target_phase",
        "owner_session_id",
        "lease_id",
        "fencing_epoch",
        "lease_expires_at",
        "lease_history",
        "last_activity_at",
        "updated_at",
        "progress",
        "context_manifests",
        "verification_history",
    }
)


Command = Union[
    AdvancePhase,
    AppendArtifactBlock,
    ClearProgress,
    BeginExecutorHandoff,
    CompleteExecutorHandoff,
    ExportArtifact,
    GenerateContextManifest,
    InitializeArtifact,
    MarkHalt,
    MarkPass,
    Reactivate,
    RecordArtifactPublication,
    RecordVerification,
    RecordExecutorStep,
    RecordSpecialistRecommendation,
    RejectExecutorHandoff,
    RenderArtifact,
    ResumeStale,
    SetExtensionFields,
    UpdateProgress,
    VerifyExecutorStep,
]


_COMMAND_TYPES = {
    AdvancePhase: "advance-phase",
    AppendArtifactBlock: "append-artifact-block",
    ClearProgress: "clear-progress",
    BeginExecutorHandoff: "executor-handoff-begin",
    CompleteExecutorHandoff: "executor-handoff-complete",
    ExportArtifact: "export-artifact",
    GenerateContextManifest: "generate-context-manifest",
    InitializeArtifact: "initialize-artifact",
    MarkHalt: "mark-halt",
    MarkPass: "mark-pass",
    Reactivate: "reactivate",
    RecordArtifactPublication: "record-artifact-publication",
    RecordVerification: "record-verification",
    RecordExecutorStep: "executor-handoff-record-step",
    RecordSpecialistRecommendation: "specialists-record-recommendation",
    RejectExecutorHandoff: "executor-handoff-reject-canonical-drift",
    RenderArtifact: "render-artifact",
    ResumeStale: "resume-stale",
    SetExtensionFields: "set-extension-fields",
    UpdateProgress: "update-progress",
    VerifyExecutorStep: "executor-handoff-verify-step",
}


def _command_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    if isinstance(value, tuple):
        return [_command_value(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: _command_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    raise TypeError("kernel-command-value-invalid")


def kernel_command_type(command: object) -> str:
    for command_class, name in _COMMAND_TYPES.items():
        if type(command) is command_class:
            return name
    raise TypeError("kernel-command-type-invalid")


def encode_kernel_command(command: object) -> FrozenJsonObject:
    """Return the one canonical immutable document for a typed command."""
    payload = _command_value(command)
    if not isinstance(payload, dict):
        raise TypeError("kernel-command-value-invalid")
    encoded = freeze_json_value(
        {
            "schema": "mission-kernel-command/1",
            "type": kernel_command_type(command),
            "value": payload,
        }
    )
    if not isinstance(encoded, FrozenJsonObject):
        raise TypeError("kernel-command-value-invalid")
    return decode_json_object(encode_json_object(encoded))
