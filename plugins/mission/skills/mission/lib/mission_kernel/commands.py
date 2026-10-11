"""Closed K2 command subset whose authority is present in MissionState."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import re
from typing import Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .guidance import GuidanceFacts

from .json_codec import decode_json_object, encode_json_object, freeze_json_value
from .model import FrozenJsonObject
from .model import HaltCategory, Phase, PreparedHandoff
from .artifact import ArtifactEffectClaim
from .a4 import SpecialistRecommendationProjection
from .fresh_review import FreshReviewRequest
from .fresh_review_completion import FreshReviewCompletionEvidence
from .fresh_review_coverage import FreshReviewBindings


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
    acceptance_candidate_digests: FrozenJsonObject = FrozenJsonObject(())
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD
    fresh_review_evidence: tuple[FreshReviewCompletionEvidence, ...] = ()
    fresh_review_bindings: FreshReviewBindings | None = None
    approval_settlement: SettleDispatchBudget | None = None


@dataclass(frozen=True)
class ReserveDispatchBudget:
    at: str
    entry: str
    target: str
    operation_id: str
    fencing_epoch: int
    policy_timeout: int
    reserved_bytes: int
    candidate_digest: str | None  # only verification's reserved observation supervisor
    fallback_reason: str | None = None
    guidance: "GuidanceFacts | None" = None


@dataclass(frozen=True)
class RecordBudgetRefusal:
    at: str
    request: ReserveDispatchBudget


@dataclass(frozen=True)
class SettleDispatchBudget:
    at: str
    reservation_id: str
    outcome: str
    elapsed_sec: int | None
    candidate_digest: str
    result_digest: str
    tool_calls: int | None = None
    replays: int | None = None
    output_bytes: int | None = None
    completed: bool = False
    refusal_reason: str | None = None
    progress_digest: str | None = None
    approval_terminal_digest: str | None = None


@dataclass(frozen=True)
class ReconcileDispatchBudget:
    at: str


@dataclass(frozen=True)
class EnterFinalPhase:
    at: str
    reason: str


@dataclass(frozen=True)
class BudgetStop:
    at: str
    scope: str
    reason_code: str


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
class DeclineSpecialistSelection:
    """Terminate the current candidate checkpoint with an explicit decision."""

    selection_id: str
    reason: str
    at: Optional[str] = None
    compatibility: CompatibilityPayload = EMPTY_COMPATIBILITY_PAYLOAD

    def __post_init__(self) -> None:
        if type(self.selection_id) is not str or re.fullmatch(
            r"sel_[0-9a-f]{32}", self.selection_id
        ) is None:
            raise TypeError("specialist-selection-id-invalid")
        if (
            not isinstance(self.reason, str)
            or not self.reason.strip()
            or len(self.reason) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in self.reason)
        ):
            raise TypeError("specialist-decline-reason-invalid")


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
class ClaimsLedgerEffectClaim:
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
class GenerateClaimsLedger:
    at: str
    iteration: int
    doc_digest: str
    effect: ClaimsLedgerEffectClaim


@dataclass(frozen=True)
class RecordVerification:
    at: str
    iteration: int
    checks: tuple[VerificationCheck, ...]
    kind: str = "execution"


@dataclass(frozen=True)
class RecordVerificationReceipt:
    """Persist a runner-produced receipt; callers cannot declare success."""

    at: str
    receipt: FrozenJsonObject
    settlement: SettleDispatchBudget | None = None


@dataclass(frozen=True)
class ImportAcceptanceContract:
    """Install one immutable acceptance contract for a mission."""

    at: str
    contract: FrozenJsonObject


@dataclass(frozen=True)
class FreshReviewInputEffectClaim:
    kind: str
    target: str
    digest: str
    size: int


@dataclass(frozen=True)
class PrepareFreshReview:
    request: FreshReviewRequest
    operation_id: str
    intent_digest: str
    payload_digest: str
    packet: Optional[FrozenJsonObject]
    effect: Optional[FreshReviewInputEffectClaim]
    repair_evidence: tuple[FreshReviewCompletionEvidence, ...] = ()


@dataclass(frozen=True)
class ImportRepairOrigins:
    evidence: tuple[FreshReviewCompletionEvidence, ...]
    fencing_epoch: int


@dataclass(frozen=True)
class WithdrawFreshReviewRequest:
    request_id: str
    operation_id: str
    fencing_epoch: int


@dataclass(frozen=True)
class BeginFreshReviewDispatch:
    request_id: str
    operation_id: str
    fencing_epoch: int
    intent_digest: str
    payload_digest: str
    dispatch: FrozenJsonObject
    candidate_digest: str


@dataclass(frozen=True)
class RecordFreshReviewLaunch:
    request_id: str
    operation_id: str
    fencing_epoch: int
    launch: FrozenJsonObject
    candidate_digest: str


@dataclass(frozen=True)
class CommitFreshReviewResult:
    request_id: str
    operation_id: str
    fencing_epoch: int
    receipt: FrozenJsonObject


@dataclass(frozen=True)
class ImportFreshReviewOutput:
    request_id: str
    operation_id: str
    fencing_epoch: int
    receipt: FrozenJsonObject
    observation: FrozenJsonObject
    budget_used: FrozenJsonObject
    candidate_digest: str
    output_base64: Optional[str]
    effect: Optional[FreshReviewInputEffectClaim]
    coverage_effect: Optional[FreshReviewInputEffectClaim] = None
    findings_effect: tuple[FreshReviewInputEffectClaim, ...] = ()
    replay_results: tuple[FrozenJsonObject, ...] = ()
    repair_evidence: tuple[FreshReviewCompletionEvidence, ...] = ()


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


class HandoffAbortReason(str, Enum):
    """Why a person ended an open handoff (#767 D3).

    Deliberately disjoint from ``CanonicalPlanRejectionCode``.  That set means
    "the canonical plan drifted", and it is what later counts of drift are read
    from.  Mixing a person's decision into it would make every abort look like
    a drift event.
    """

    EXECUTOR_ABANDONED = "executor-abandoned"
    PLAN_SUPERSEDED = "plan-superseded"
    OPERATOR_ABORT = "operator-abort"


@dataclass(frozen=True)
class AbortExecutorHandoff:
    at: str
    reason: HandoffAbortReason


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
        "claims_ledgers",
        "verification_history",
        "verification_receipts",
        "acceptance_contract",
        "fresh_review",
        "repair_lineage",
        "budget_ledger",
    }
)


Command = Union[
    AbortExecutorHandoff,
    AdvancePhase,
    AppendArtifactBlock,
    ClearProgress,
    BeginExecutorHandoff,
    CompleteExecutorHandoff,
    DeclineSpecialistSelection,
    ExportArtifact,
    GenerateContextManifest,
    GenerateClaimsLedger,
    InitializeArtifact,
    MarkHalt,
    MarkPass,
    ReserveDispatchBudget,
    RecordBudgetRefusal,
    SettleDispatchBudget,
    ReconcileDispatchBudget,
    EnterFinalPhase,
    BudgetStop,
    Reactivate,
    RecordArtifactPublication,
    RecordVerification,
    RecordVerificationReceipt,
    ImportAcceptanceContract,
    PrepareFreshReview,
    ImportRepairOrigins,
    BeginFreshReviewDispatch,
    WithdrawFreshReviewRequest,
    RecordFreshReviewLaunch,
    CommitFreshReviewResult,
    ImportFreshReviewOutput,
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
    AbortExecutorHandoff: "executor-handoff-abort",
    AdvancePhase: "advance-phase",
    AppendArtifactBlock: "append-artifact-block",
    ClearProgress: "clear-progress",
    BeginExecutorHandoff: "executor-handoff-begin",
    CompleteExecutorHandoff: "executor-handoff-complete",
    DeclineSpecialistSelection: "decline-specialist-selection",
    ExportArtifact: "export-artifact",
    GenerateContextManifest: "generate-context-manifest",
    GenerateClaimsLedger: "generate-claims-ledger",
    InitializeArtifact: "initialize-artifact",
    MarkHalt: "mark-halt",
    MarkPass: "mark-pass",
    ReserveDispatchBudget: "budget-reserve-dispatch",
    RecordBudgetRefusal: "budget-record-refusal",
    SettleDispatchBudget: "budget-settle-dispatch",
    ReconcileDispatchBudget: "budget-reconcile",
    EnterFinalPhase: "budget-enter-final",
    BudgetStop: "budget-stop",
    Reactivate: "reactivate",
    RecordArtifactPublication: "record-artifact-publication",
    RecordVerification: "record-verification",
    RecordVerificationReceipt: "record-verification-receipt",
    ImportAcceptanceContract: "acceptance-contract-import",
    PrepareFreshReview: "fresh-review-prepare",
    ImportRepairOrigins: "repair-origins-import",
    BeginFreshReviewDispatch: "fresh-review-run",
    WithdrawFreshReviewRequest: "fresh-review-withdraw",
    RecordFreshReviewLaunch: "fresh-review-launch",
    CommitFreshReviewResult: "fresh-review-result",
    ImportFreshReviewOutput: "fresh-review-output-import",
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
    # Guidance binds a reader-issued snapshot object.  Its public projection is
    # closed and portable; recursively walking the dataclass would otherwise
    # serialize its opaque private binding.
    from .guidance import GuidanceFacts, guidance_payload
    if isinstance(value, GuidanceFacts):
        return guidance_payload(value)
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


def kernel_command_type_names() -> frozenset:
    """Return every type name a typed kernel command can carry.

    The names are the closed vocabulary of the decision table.  A document
    that claims the kernel command schema but names something outside this
    set was not produced by ``encode_kernel_command``, so nothing may be
    inferred about the shape of its fields.
    """
    return frozenset(_COMMAND_TYPES.values())


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
