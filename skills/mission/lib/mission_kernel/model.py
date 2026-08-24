"""Deeply immutable read model for versioned mission state documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from .a4 import A4Projection


class SchemaOrigin(str, Enum):
    MISSING = "missing"
    V1 = "v1"
    V2 = "v2"
    V3 = "v3"
    V4 = "v4"
    V5 = "v5"


class Phase(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    SCORING = "scoring"
    DONE = "done"
    HALTED = "halted"


class TerminalOutcome(str, Enum):
    COMPLETED_PASS = "completed_pass"
    COMPLETED_EVIDENCE = "completed_evidence"
    BLOCKED_EXTERNAL = "blocked_external"
    AWAITING_APPROVAL = "awaiting_approval"
    STALE_SUPERSEDED = "stale_superseded"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    USER_ABORTED = "user_aborted"
    ROUTED_ELSEWHERE = "routed_elsewhere"


class PlanSource(str, Enum):
    CORE = "core"
    PROVIDER = "provider"


class HandoffStatus(str, Enum):
    PREPARED = "prepared"
    CONSUMING = "consuming"
    CONSUMED = "consumed"
    REJECTED = "rejected"


class ReviewKind(str, Enum):
    REVIEW_INPUT = "review-input"
    REVIEW_AGGREGATE = "review-aggregate"


class FindingSeverity(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class FindingStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class ScoreSource(str, Enum):
    LEGACY_UNVERIFIED = "legacy-unverified"
    SCORING_JSON = "scoring-json"
    MANUAL_IMPORT = "manual-import"


class RevisionScopeKind(str, Enum):
    GIT = "git"
    NOT_APPLICABLE = "not-applicable"


class LeaseKind(str, Enum):
    LEGACY_ABSENT = "legacy-absent"
    FENCED = "fenced"


class SessionRole(str, Enum):
    IMPLEMENTER = "implementer"
    CHECKER = "checker"
    PLANNING = "planning"
    ANALYZE = "analyze"
    RELEASE = "release"


class HaltCategory(str, Enum):
    BLOCKED_EXTERNAL = "blocked-external"
    AWAITING_APPROVAL = "awaiting-approval"
    PARTIAL_DONE = "partial-done"
    EVIDENCE_SUBMITTED = "evidence-submitted"
    ROUTED_GOAL = "routed-goal"
    STAGNATION = "stagnation"
    USER_ABORT = "user-abort"
    STALE = "stale"
    OTHER = "other"


@dataclass(frozen=True)
class SnapshotProvenance:
    schema_origin: SchemaOrigin
    session_id: Optional[str]
    document_digest: str
    generation: Optional[int] = None
    commit_digest: Optional[str] = None

    def __post_init__(self) -> None:
        digest = re.compile(r"sha256:[0-9a-f]{64}\Z")
        lineage_is_paired = (self.generation is None) == (self.commit_digest is None)
        generation_is_valid = (
            self.generation is None
            or (type(self.generation) is int and self.generation >= 0)
        )
        commit_is_valid = (
            self.commit_digest is None
            or digest.fullmatch(self.commit_digest) is not None
        )
        if (
            not isinstance(self.schema_origin, SchemaOrigin)
            or digest.fullmatch(self.document_digest) is None
            or not lineage_is_paired
            or not generation_is_valid
            or not commit_is_valid
        ):
            raise ValueError("invalid-snapshot-provenance")


@dataclass(frozen=True)
class FrozenJsonObject:
    items: tuple[tuple[str, "FrozenJsonValue"], ...]

    def thaw(self) -> dict[str, object]:
        return {key: thaw_json_value(value) for key, value in self.items}


JsonScalar = Union[None, bool, int, float, str]
FrozenJsonValue = Union[JsonScalar, FrozenJsonObject, tuple["FrozenJsonValue", ...]]


@dataclass(frozen=True)
class MissionIdentity:
    mission: Optional[str]
    mission_id: Optional[str]
    session_id: Optional[str]


@dataclass(frozen=True)
class MissionControl:
    phase: Phase
    terminal_outcome: Optional[TerminalOutcome]
    iteration: int
    max_iter: Optional[int]
    threshold: Optional[float]
    reviewer_count: int
    stagnation_count: int
    loop_active: bool
    passes: bool
    halt_reason: str
    halt_category: Optional[HaltCategory]
    session_role: SessionRole


@dataclass(frozen=True)
class AbsentPlan:
    kind: str = "absent"


@dataclass(frozen=True)
class CorePlan:
    schema: str
    path: str
    digest: str
    source_id: str
    source_digest: str
    selection_source: str
    iteration: int
    generation: int
    validated_at: str

    @property
    def kind(self) -> str:
        return PlanSource.CORE.value

    @property
    def source(self) -> PlanSource:
        return PlanSource.CORE


@dataclass(frozen=True)
class ProviderPlan:
    schema: str
    path: str
    digest: str
    source_id: str
    source_digest: str
    selection_source: str
    iteration: int
    generation: int
    validated_at: str

    @property
    def kind(self) -> str:
        return PlanSource.PROVIDER.value

    @property
    def source(self) -> PlanSource:
        return PlanSource.PROVIDER


PlanRecord = Union[CorePlan, ProviderPlan]
Plan = Union[AbsentPlan, CorePlan, ProviderPlan]


@dataclass(frozen=True)
class AbsentHandoff:
    kind: str = "absent"


@dataclass(frozen=True)
class PreparedHandoff:
    schema: str
    handoff_id: str
    plan: PlanRecord
    ordered_step_ids: tuple[str, ...]

    @property
    def kind(self) -> HandoffStatus:
        return HandoffStatus.PREPARED


@dataclass(frozen=True)
class ConsumingHandoff:
    schema: str
    handoff_id: str
    plan: PlanRecord
    ordered_step_ids: tuple[str, ...]
    begun_at: str

    @property
    def kind(self) -> HandoffStatus:
        return HandoffStatus.CONSUMING


@dataclass(frozen=True)
class ConsumedHandoff:
    schema: str
    handoff_id: str
    plan: PlanRecord
    ordered_step_ids: tuple[str, ...]
    begun_at: str
    consumed_at: str

    @property
    def kind(self) -> HandoffStatus:
        return HandoffStatus.CONSUMED


@dataclass(frozen=True)
class RejectedHandoff:
    schema: str
    handoff_id: str
    plan: PlanRecord
    ordered_step_ids: tuple[str, ...]
    rejected_reason: str
    begun_at: Optional[str] = None

    @property
    def kind(self) -> HandoffStatus:
        return HandoffStatus.REJECTED


Handoff = Union[
    AbsentHandoff,
    PreparedHandoff,
    ConsumingHandoff,
    ConsumedHandoff,
    RejectedHandoff,
]


@dataclass(frozen=True)
class NotApplicableRevisionScope:
    reason_code: str
    kind: RevisionScopeKind = RevisionScopeKind.NOT_APPLICABLE


@dataclass(frozen=True)
class GitRevisionScope:
    base_sha: str
    head_sha: str
    kind: RevisionScopeKind = RevisionScopeKind.GIT


RevisionScope = Union[NotApplicableRevisionScope, GitRevisionScope]


@dataclass(frozen=True)
class ReviewInputRef:
    relative_path: str
    digest: str
    size: int
    iteration: int
    perspective: str
    kind: ReviewKind = ReviewKind.REVIEW_INPUT


@dataclass(frozen=True)
class ReviewAggregateRef:
    relative_path: str
    digest: str
    generation: str
    revision_scope: RevisionScope
    size: Optional[int] = None
    iteration: Optional[int] = None
    review_group_id: Optional[str] = None
    review_generation: Optional[int] = None
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None
    kind: ReviewKind = ReviewKind.REVIEW_AGGREGATE


ReviewRef = Union[ReviewInputRef, ReviewAggregateRef]


@dataclass(frozen=True)
class ContentAddressedRef:
    kind: str
    relative_path: str
    digest: str
    size: Optional[int] = None


@dataclass(frozen=True)
class ManualScoreRef:
    relative_path: str
    digest: str
    generation: str
    revision_scope: RevisionScope
    size: Optional[int] = None
    kind: str = "manual-score"


@dataclass(frozen=True)
class FindingResolutionRef:
    relative_path: str
    digest: str
    size: int
    kind: str = "finding-resolution"


@dataclass(frozen=True)
class FindingIdentity:
    id: str
    generation: int


@dataclass(frozen=True)
class OpenFinding:
    id: str
    generation: int
    iteration: int
    reviewer: str
    severity: FindingSeverity
    axis: str
    summary: str
    recommendation: str
    evidence_ref: ReviewRef
    status: FindingStatus = FindingStatus.OPEN
    legacy_payload: Optional[FrozenJsonObject] = None


@dataclass(frozen=True)
class ResolvedFinding:
    id: str
    generation: int
    iteration: int
    reviewer: str
    severity: FindingSeverity
    axis: str
    summary: str
    recommendation: str
    evidence_ref: ReviewRef
    prior_identity: FindingIdentity
    resolution_evidence_ref: FindingResolutionRef
    resolved_at: str
    status: FindingStatus = FindingStatus.RESOLVED


Finding = Union[OpenFinding, ResolvedFinding]


@dataclass(frozen=True)
class LegacyFindingsUnloaded:
    review_refs: tuple[ReviewRef, ...]


@dataclass(frozen=True)
class MaterializedFindings:
    findings: tuple[Finding, ...]
    review_refs: tuple[ReviewRef, ...] = ()


FindingCollection = Union[LegacyFindingsUnloaded, MaterializedFindings]


@dataclass(frozen=True)
class LegacyScore:
    authoritative: bool
    payload: FrozenJsonObject


@dataclass(frozen=True)
class BoundScore:
    authoritative: bool
    source: ScoreSource
    payload: FrozenJsonObject
    source_evidence_ref: Union[ReviewAggregateRef, ManualScoreRef]
    scoring_evidence_ref: ContentAddressedRef
    revision_scope: RevisionScope

    @property
    def review_evidence_ref(self) -> Optional[ReviewAggregateRef]:
        if isinstance(self.source_evidence_ref, ReviewAggregateRef):
            return self.source_evidence_ref
        return None

    @property
    def manual_evidence_ref(self) -> Optional[ManualScoreRef]:
        if isinstance(self.source_evidence_ref, ManualScoreRef):
            return self.source_evidence_ref
        return None


Score = Union[LegacyScore, BoundScore]


@dataclass(frozen=True)
class LegacyAbsentLease:
    kind: LeaseKind = LeaseKind.LEGACY_ABSENT


@dataclass(frozen=True)
class LeaseHistoryEntry:
    owner_session_id: str
    lease_id: str
    fencing_epoch: int
    reason: str
    at: str


@dataclass(frozen=True)
class FencedLease:
    owner_session_id: str
    lease_id: str
    fencing_epoch: int
    lease_expires_at: str
    lease_history: tuple[LeaseHistoryEntry, ...]
    kind: LeaseKind = LeaseKind.FENCED


Lease = Union[LegacyAbsentLease, FencedLease]


@dataclass(frozen=True)
class MissionState:
    schema_origin: SchemaOrigin
    identity: MissionIdentity
    control: MissionControl
    plan: Plan
    handoff: Handoff
    reviews: tuple[ReviewRef, ...]
    findings: FindingCollection
    scores: tuple[Score, ...]
    lease: Lease
    extensions: FrozenJsonObject
    legacy_passthrough: Optional[FrozenJsonObject]
    a4: "A4Projection" = field(default_factory=lambda: _empty_a4_projection())
    snapshot_provenance: Optional[SnapshotProvenance] = None
    _snapshot_binding: Optional[object] = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def terminal_outcome(self) -> Optional[TerminalOutcome]:
        return self.control.terminal_outcome


def _empty_a4_projection() -> "A4Projection":
    from .a4 import EMPTY_A4_PROJECTION

    return EMPTY_A4_PROJECTION


def thaw_json_value(value: FrozenJsonValue) -> object:
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value
