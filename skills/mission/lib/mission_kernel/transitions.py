"""One deterministic table for the K2 state-only command subset."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from pathlib import Path
import re
import weakref
from typing import Any, Callable, Mapping, Optional, Type

from mission_common import terminal_outcome_for_halt
from plan_contract import PlanContractError, canonical_plan_bytes

from .commands import (
    ClearProgress,
    CompatibilityPayload,
    ContextManifestEffectClaim,
    ClaimsLedgerEffectClaim,
    DeclineSpecialistSelection,
    GENERIC_SET_DEDICATED_FIELDS,
    GENERIC_SET_FROZEN_FIELDS,
    AdvancePhase,
    AppendArtifactBlock,
    BeginExecutorHandoff,
    CanonicalPlanObservation,
    CanonicalPlanRejectionCode,
    Command,
    CompleteExecutorHandoff,
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
    BudgetStop as BudgetStopCommand,
    Reactivate,
    RecordArtifactPublication,
    RecordExecutorStep,
    RecordSpecialistRecommendation,
    RecordVerification,
    RecordVerificationReceipt,
    ImportAcceptanceContract,
    PrepareFreshReview,
    BeginFreshReviewDispatch,
    WithdrawFreshReviewRequest,
    RecordFreshReviewLaunch,
    CommitFreshReviewResult,
    ImportFreshReviewOutput,
    RejectExecutorHandoff,
    AbortExecutorHandoff,
    HandoffAbortReason,
    RenderArtifact,
    ResumeStale,
    SetExtensionFields,
    ProgressEffectClaim,
    UpdateProgress,
    VerifyExecutorStep,
)
from .evidence import (
    EvidenceRuleError,
    apply_context_manifest,
    apply_claims_ledger,
    apply_progress_clear,
    apply_progress_update,
    apply_verification_record,
    apply_verification_receipt,
)

from .a4 import (
    A4ProjectionError,
    A4Projection,
    ExecutorStepDecision,
    SpecialistRecommendationProjection,
    SpecialistSelectionProjection,
    decode_v4_a4_projection,
    validate_specialist_recommendation_projection,
    validate_specialist_recommendation_shape,
)
from provider_public_contract import SpecialistPublicContractError
from .artifact import (
    ArtifactRuleError,
    append_artifact_block_document,
    export_artifact_document,
    initialize_artifact_document,
    record_artifact_publication_document,
    render_artifact_document,
)
from .json_codec import freeze_json_value
from .model import (
    AbsentHandoff,
    AbsentPlan,
    BoundScore,
    ConsumedHandoff,
    ConsumingHandoff,
    FrozenJsonObject,
    HaltCategory,
    MissionControl,
    MissionState,
    Phase,
    PreparedHandoff,
    RejectedHandoff,
    SessionRole,
    TerminalOutcome,
)
from .budget import BudgetError, Settlement, decode_ledger, ledger_document
from .budget_decisions import (Admission, admit, completion_rejection, enter_final, expire_reservations, record_exhaustion,
    reserve, settle, stop, with_clock)


class TransitionTableError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Rejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_A4_OWNED_LEGACY_FIELDS = frozenset(
    {
        "decisions",
        "task_profile",
        "specialist_registry_projection",
        "specialists_decision",
        "planning_provider_binding",
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
        "specialists_ineligible",
        "specialists_phase_plan",
        "specialist_invocations",
        "specialists_mode",
        "planning_policy_version",
        "planning_strategy",
        "planning_contract_digest",
    }
)


def _unbound_state(state: MissionState, **changes: Any) -> MissionState:
    return replace(state, snapshot_provenance=None, **changes)


def _with_budget(state: MissionState, ledger: object) -> MissionState:
    """Update the typed projection and every authoritative wire backing together."""
    # The ledger is bound to the session's budget_minutes; check the round trip with it.
    source = state.legacy_passthrough if state.legacy_passthrough is not None else state.extensions
    minutes = source.thaw().get('budget_minutes')
    try:
        # Encoding validates caller text (reasons, scopes); a bad value is a rejection.
        document = ledger_document(ledger)
        if decode_ledger({'budget_minutes': minutes, 'budget_ledger': document}) != ledger:
            raise _Rejected('budget-projection-backing-invalid')
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    extensions = state.extensions.thaw()
    extensions['budget_ledger'] = document
    frozen_extensions = freeze_json_value(extensions)
    if not isinstance(frozen_extensions, FrozenJsonObject):
        raise _Rejected('budget-projection-backing-invalid')
    changes: dict[str, Any] = {
        'budget': ledger,
        'extensions': frozen_extensions,
    }
    if state.legacy_passthrough is not None:
        legacy = state.legacy_passthrough.thaw()
        legacy['budget_ledger'] = document
        frozen_legacy = freeze_json_value(legacy)
        if not isinstance(frozen_legacy, FrozenJsonObject):
            raise _Rejected('budget-projection-backing-invalid')
        changes['legacy_passthrough'] = frozen_legacy
    return _unbound_state(state, **changes)


def _budget_guard(function, *args):
    """Evaluate a budget guard; malformed command input is a rejection, not a crash."""
    try:
        return function(*args)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc


def _budget_at(command: object) -> str:
    at = getattr(command, 'at', None)
    if not isinstance(at, str):
        raise _Rejected('budget-command-time-invalid')
    return at


def _observe_budget(state: MissionState, at: str, *, active: bool | None = None) -> MissionState:
    ledger = state.budget
    if ledger.policy is None:
        return state
    try:
        if active is None:
            ledger = record_exhaustion(ledger, at)
        else:
            ledger = record_exhaustion(expire_reservations(with_clock(ledger, at, active=active), at), at)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    return _with_budget(state, ledger)


def _prepare_budget_admission(state: MissionState, command: ReserveDispatchBudget):
    """Prepare the one authoritative admission snapshot for reserve/refusal.

    Refusal persistence is a reducer operation, but it must describe the same
    attempted admission as a reservation would.  In particular capacity is a
    property of the encoded, post-clock/latch snapshot, not caller input.
    """
    if state.budget.policy is None:
        raise _Rejected('budget-policy-absent')
    if state.control.loop_active is not True:
        raise _Rejected('budget-loop-inactive')
    try:
        observed = expire_reservations(with_clock(state.budget, command.at, active=state.control.loop_active), command.at)
        if observed.stop_slots.final_latch is None:
            from .budget import deadlines
            if command.at >= deadlines(observed, command.at).repair:
                from .budget import FinalLatch
                observed = replace(observed, stop_slots=replace(observed.stop_slots,
                    final_latch=FinalLatch(command.at, 'repair-deadline')))
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    # E0 remains the capacity authority.  This reducer supplies only the
    # actual persisted document and F's bounded reservation row delta.
    try:
        from .codec_v4 import project_legacy_document
        from .state_capacity import BUDGET_RESERVATION_ROW_DELTA, StateEncoding, state_capacity_verdict
        if state.schema_origin.value == 'v5':
            from .codec_v5 import encode_v5_state
            from .guidance import GuidanceFacts
            if not isinstance(command.guidance, GuidanceFacts):
                raise _Rejected('budget-capacity-evidence-required')
            observed_state = _with_budget(state, observed)
            encoded_payload = encode_v5_state(observed_state, command.guidance)
            payload = json.loads(encoded_payload)
            encoded = len(encoded_payload)
            encoding = StateEncoding.CANONICAL
        else:
            observed_state = _with_budget(state, observed)
            payload = json.loads(project_legacy_document(observed_state))
            encoded = len(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode('utf-8'))
            encoding = StateEncoding.LEGACY_PRETTY
        verdict = state_capacity_verdict(None, payload, encoded, encoding=encoding)
        from .budget_decisions import CapacityEvidence
        capacity = CapacityEvidence(verdict.metrics.headroom, BUDGET_RESERVATION_ROW_DELTA)
    except _Rejected:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _Rejected('budget-capacity-evidence-invalid') from exc
    admission = admit(observed, state, command.at, entry=command.entry, target=command.target,
        operation_id=command.operation_id, fencing_epoch=command.fencing_epoch,
        policy_timeout=command.policy_timeout, reserved_bytes=command.reserved_bytes,
        candidate_digest=command.candidate_digest, fallback_reason=command.fallback_reason, capacity=capacity)
    return observed, admission


def _reserve_budget(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, ReserveDispatchBudget)
    observed, admission = _prepare_budget_admission(state, command)
    if hasattr(admission, 'reason'):
        raise _Rejected(admission.reason)
    try:
        ledger = reserve(observed, admission)
        ledger = record_exhaustion(expire_reservations(ledger, command.at), command.at)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    if admission.replay:
        if admission.saved_settlement is None:
            raise _Rejected('budget-replay-settlement-missing')
        return Transition(_with_budget(state, ledger),
                          (BudgetDispatchReplayed('budget-dispatch-replayed', admission.saved_settlement),))
    return Transition(_with_budget(state, ledger), (KernelEvent('budget-dispatch-reserved'),))


def _record_budget_refusal(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, RecordBudgetRefusal)
    request = command.request
    if not isinstance(request, ReserveDispatchBudget) or request.at != command.at:
        raise _Rejected('budget-refusal-request-invalid')
    try:
        _observed, result = _prepare_budget_admission(state, request)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    if isinstance(result, Admission):
        raise _Rejected('budget-refusal-not-current')
    if result.ledger is None:
        raise _Rejected('budget-refusal-unrecordable')
    return Transition(_with_budget(state, result.ledger), (KernelEvent('budget-dispatch-refused'),))


def _settle_budget(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, SettleDispatchBudget)
    if state.budget.policy is None:
        raise _Rejected('budget-policy-absent')
    try:
        observed = with_clock(state.budget, command.at, active=state.control.loop_active)
        ledger = settle(observed, command.at, reservation_id=command.reservation_id,
            outcome=command.outcome, elapsed_sec=command.elapsed_sec, candidate_digest=command.candidate_digest,
            result_digest=command.result_digest, tool_calls=command.tool_calls, replays=command.replays,
            output_bytes=command.output_bytes, completed=command.completed, refusal_reason=command.refusal_reason)
        ledger = record_exhaustion(expire_reservations(ledger, command.at), command.at)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    return Transition(_with_budget(state, ledger), (KernelEvent('budget-dispatch-settled'),))


def _reconcile_budget(state: MissionState, command: object) -> Transition:
    assert isinstance(command, ReconcileDispatchBudget)
    if state.budget.policy is None:
        raise _Rejected('budget-policy-absent')
    try:
        ledger = with_clock(state.budget, command.at, active=state.control.loop_active)
        ledger = record_exhaustion(expire_reservations(ledger, command.at), command.at)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    return Transition(_with_budget(state, ledger), (KernelEvent('budget-reconciled'),))


def _enter_final_budget(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, EnterFinalPhase)
    if state.budget.policy is None:
        raise _Rejected('budget-policy-absent')
    try:
        ledger = enter_final(expire_reservations(with_clock(state.budget, command.at, active=state.control.loop_active), command.at), command.at, command.reason)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    return Transition(_with_budget(state, ledger), (KernelEvent('budget-final-entered'),))


def _budget_stop(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, BudgetStopCommand)
    if state.budget.policy is None:
        raise _Rejected('budget-policy-absent')
    try:
        ledger = stop(expire_reservations(with_clock(state.budget, command.at, active=False), command.at), command.at, command.scope, command.reason_code)
    except BudgetError as exc:
        raise _Rejected(str(exc)) from exc
    control = _active_control(state)
    outcome = TerminalOutcome(terminal_outcome_for_halt(
        HaltCategory.PARTIAL_DONE.value, control.session_role.value, superseded=False))
    new_control = replace(control, phase=Phase.HALTED, loop_active=False,
        halt_reason=command.reason_code, halt_category=HaltCategory.PARTIAL_DONE,
        terminal_outcome=outcome)
    lifecycle = _apply_compatibility(_unbound_state(state, control=new_control), MarkHalt,
        CompatibilityPayload(), at=command.at, dedicated_upserts={
            'phase': Phase.HALTED.value, 'loop_active': False, 'halt_reason': command.reason_code})
    return Transition(_with_budget(lifecycle, ledger),
                      (KernelEvent('budget-stopped'), KernelEvent('mission-halted')))


def _a4_authority_document(state: MissionState) -> dict[str, object]:
    return (
        state.legacy_passthrough.thaw()
        if state.legacy_passthrough is not None
        else state.extensions.thaw()
    )


def _sync_a4_projection(state: MissionState) -> MissionState:
    try:
        projection = decode_v4_a4_projection(_a4_authority_document(state), state.handoff)
    except A4ProjectionError as exc:
        raise _Rejected(exc.code) from exc
    return _unbound_state(state, a4=projection)


@dataclass(frozen=True)
class KernelEvent:
    type: str


@dataclass(frozen=True)
class BudgetDispatchReplayed(KernelEvent):
    """A retained D/E settlement answers a replay without authorizing spawn."""
    settlement: Settlement


@dataclass(frozen=True)
class Transition:
    new_state: MissionState
    events: tuple[KernelEvent, ...]
    effects: tuple[object, ...] = ()
    _seal: object | None = field(default=None, init=False, repr=False, compare=False)
    _input_state: MissionState | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _command: Command | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class Rejection:
    code: str


@dataclass(frozen=True)
class Decision:
    accepted: bool
    transition: Optional[Transition]
    rejection: Optional[Rejection]
    rule_id: Optional[str] = None

    @property
    def events(self) -> tuple[KernelEvent, ...]:
        return () if self.transition is None else self.transition.events

    @property
    def effects(self) -> tuple[object, ...]:
        return () if self.transition is None else self.transition.effects


@dataclass(frozen=True)
class TransitionRule:
    rule_id: str
    command_type: Type[object]
    command_guard: Callable[[MissionState, object], bool]
    reducer: Optional[Callable[[MissionState, object], Transition]]
    guidance_rank: Optional[int] = None
    guidance_guard: Optional[Callable[[MissionState, object], bool]] = None
    guidance_factory: Optional[Callable[[MissionState, object, str], object]] = None
    continuation_contracts: tuple[tuple[str, str, str], ...] = ()


def build_transition_table(rules: tuple[TransitionRule, ...]) -> tuple[TransitionRule, ...]:
    identifiers = set()
    command_types = set()
    for rule in rules:
        if rule.rule_id in identifiers:
            raise TransitionTableError("duplicate-rule-id")
        if not isinstance(rule.command_type, type) or not callable(rule.command_guard):
            raise TransitionTableError("incomplete-command-rule")
        if rule.reducer is not None and rule.command_type in command_types:
            raise TransitionTableError("duplicate-command-rule")
        guidance_metadata = (
            rule.guidance_rank is not None,
            rule.guidance_guard is not None,
            rule.guidance_factory is not None,
        )
        if any(guidance_metadata) and not all(guidance_metadata):
            raise TransitionTableError("incomplete-guidance-rule")
        if all(guidance_metadata):
            if type(rule.guidance_rank) is not int:
                raise TransitionTableError("invalid-guidance-rank")
            if any(
                existing.guidance_rank == rule.guidance_rank
                for existing in rules
                if existing is not rule
            ):
                raise TransitionTableError("equal-rank-primary-tie")
        continuation_ids = set()
        for contract in rule.continuation_contracts:
            if (
                not isinstance(contract, tuple)
                or len(contract) != 3
                or any(not isinstance(value, str) or not value for value in contract)
            ):
                raise TransitionTableError("invalid-continuation-contract")
            if contract[0] in continuation_ids:
                raise TransitionTableError("duplicate-continuation-contract")
            continuation_ids.add(contract[0])
        if rule.continuation_contracts and rule.guidance_rank is None:
            raise TransitionTableError("invalid-continuation-contract")
        identifiers.add(rule.rule_id)
        if rule.reducer is not None:
            command_types.add(rule.command_type)
    return rules


def _active_control(state: MissionState) -> MissionControl:
    if state.terminal_outcome is not None or state.control.phase in {Phase.DONE, Phase.HALTED}:
        raise _Rejected("terminal-state")
    if state.control.passes or state.control.halt_reason:
        raise _Rejected("terminal-state")
    return state.control


_COMPATIBILITY_FORBIDDEN_FIELDS = frozenset(
    {
        "fresh_review",
        "budget_ledger",
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "terminal_outcome",
        "mission",
        "mission_id",
        "session_id",
        "owner_session_id",
        "lease_id",
        "fencing_epoch",
        "lease_expires_at",
        "lease_history",
        "score_history",
        "failure_ledger",
    }
)

_TIMING_ACTIVITY_FIELDS = frozenset(
    {
        "phase_started_at",
        "phase_durations_sec",
        "resume_target_phase",
        "activity_current",
        "activity_segments",
        "activity_rollup",
        "activity_last_event_at",
        "activity_last_event_phase",
        "activity_anomaly_counts",
        "activity_unobserved_gap_sec",
        "activity_unobserved_gap_reasons_sec",
    }
)

_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "project_root",
        "pid",
        "pid_source",
        "hostname",
        "session_id",
        "agent",
        "created_at_session",
        "cli_version",
    }
)

_COMPATIBILITY_FIELDS = {
    AdvancePhase: _TIMING_ACTIVITY_FIELDS
    | _METADATA_FIELDS
    | frozenset(
        {
            "artifact",
            "artifact_applicability",
            "artifact_lint",
            "artifact_lint_identity",
            "artifact_lint_status",
            "executor_handoff",
        }
    ),
    MarkHalt: _TIMING_ACTIVITY_FIELDS
    | _METADATA_FIELDS
    | frozenset(
        {
            "goal_dispatch_effective",
            "goal_dispatch_host",
            "goal_dispatch_fallback_reason",
        }
    ),
    Reactivate: _TIMING_ACTIVITY_FIELDS | _METADATA_FIELDS | frozenset({"reactivation_history"}),
    ResumeStale: _TIMING_ACTIVITY_FIELDS | _METADATA_FIELDS,
    MarkPass: _TIMING_ACTIVITY_FIELDS
    | _METADATA_FIELDS
    | frozenset(
        {
            "passes_forced",
            "force_reason",
            "force_approved_by_user",
            "force_approval",
            "specialist_waiver",
            "early_stop_evaluation",
        }
    ),
    SetExtensionFields: frozenset(
        {
            "review_tier",
            "review_tier_source",
            "review_tier_signals",
            "review_tier_signal_details",
            "reviewer_count",
        }
    )
    | _TIMING_ACTIVITY_FIELDS
    | _METADATA_FIELDS,
}

_PERMISSION_PHASES = frozenset({"planning", "executing", "reviewing", "scoring"})
_PERMISSION_ACTIVITY_PHASES = _PERMISSION_PHASES | {"unknown"}
_PERMISSION_SEGMENT_KEYS = frozenset(
    {
        "kind",
        "phase",
        "reason",
        "started_at",
        "ended_at",
        "duration_sec",
        "detail",
        "iteration",
    }
)
_PERMISSION_ROLLUP_KEYS = frozenset(
    {
        "observed_total_sec",
        "closed_segment_count",
        "activity_duration_totals_sec",
        "phase_activity_duration_totals_sec",
        "wait_reason_totals_sec",
    }
)
_PERMISSION_ANOMALY_KEYS = frozenset(
    {"invalid-current-terminal", "invalid-phase-terminal"}
)


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _permission_numeric_map(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, dict) and all(
        key in allowed and _finite_nonnegative(item)
        for key, item in value.items()
    )


def _permission_timestamp(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value)
    )


def _valid_permission_segment(value: object) -> bool:
    from activity_segments import ACTIVITY_KINDS, ACTIVITY_REASONS_BY_KIND

    if not isinstance(value, dict) or not set(value).issubset(_PERMISSION_SEGMENT_KEYS):
        return False
    required = {"kind", "phase", "reason", "started_at", "ended_at", "duration_sec"}
    if not required.issubset(value):
        return False
    kind = value.get("kind")
    reason = value.get("reason")
    if (
        kind not in ACTIVITY_KINDS
        or reason not in ACTIVITY_REASONS_BY_KIND[kind]
        or value.get("phase") not in _PERMISSION_ACTIVITY_PHASES
        or not _permission_timestamp(value.get("started_at"))
        or not _permission_timestamp(value.get("ended_at"))
        or not _finite_nonnegative(value.get("duration_sec"))
    ):
        return False
    detail = value.get("detail")
    if detail is not None and (
        not isinstance(detail, str)
        or not detail
        or len(detail) > 160
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in detail)
    ):
        return False
    iteration = value.get("iteration")
    return iteration is None or (
        isinstance(iteration, int) and not isinstance(iteration, bool) and iteration > 0
    )


def _valid_permission_rollup(value: object) -> bool:
    from activity_segments import ACTIVITY_KINDS, ACTIVITY_REASONS_BY_KIND, WAIT_KINDS

    if not isinstance(value, dict) or set(value) != _PERMISSION_ROLLUP_KEYS:
        return False
    count = value.get("closed_segment_count")
    if (
        not _finite_nonnegative(value.get("observed_total_sec"))
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not _permission_numeric_map(
            value.get("activity_duration_totals_sec"), frozenset(ACTIVITY_KINDS)
        )
    ):
        return False
    phase_totals = value.get("phase_activity_duration_totals_sec")
    if not isinstance(phase_totals, dict) or any(
        phase not in _PERMISSION_ACTIVITY_PHASES
        or not _permission_numeric_map(totals, frozenset(ACTIVITY_KINDS))
        for phase, totals in phase_totals.items()
    ):
        return False
    wait_totals = value.get("wait_reason_totals_sec")
    return isinstance(wait_totals, dict) and all(
        kind in WAIT_KINDS
        and _permission_numeric_map(
            totals, frozenset(ACTIVITY_REASONS_BY_KIND[kind])
        )
        for kind, totals in wait_totals.items()
    )


def _validate_permission_compatibility(command: MarkHalt) -> None:
    if type(command.permission_observation) is not bool:
        raise _Rejected("permission-transition-invalid")
    if not command.permission_observation:
        return
    if (
        command.category is not HaltCategory.BLOCKED_EXTERNAL
        or not _permission_timestamp(command.at)
    ):
        raise _Rejected("permission-transition-invalid")
    from activity_segments import RECENT_SEGMENT_LIMIT

    payload = command.compatibility.upserts.thaw()
    requested = set(payload) | set(command.compatibility.removals)
    if requested - (_TIMING_ACTIVITY_FIELDS | _METADATA_FIELDS):
        raise _Rejected("permission-transition-invalid")
    if "resume_target_phase" in payload:
        raise _Rejected("permission-transition-invalid")
    if "phase_started_at" in payload and payload["phase_started_at"] != command.at:
        raise _Rejected("permission-transition-invalid")
    if "activity_current" in payload and payload["activity_current"] is not None:
        raise _Rejected("permission-transition-invalid")
    durations = payload.get("phase_durations_sec")
    if durations is not None and not _permission_numeric_map(durations, _PERMISSION_PHASES):
        raise _Rejected("permission-transition-invalid")
    anomalies = payload.get("activity_anomaly_counts")
    if anomalies is not None and (
        not isinstance(anomalies, dict)
        or any(
            key not in _PERMISSION_ANOMALY_KEYS
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in anomalies.items()
        )
    ):
        raise _Rejected("permission-transition-invalid")
    segments = payload.get("activity_segments")
    if segments is not None and (
        not isinstance(segments, list)
        or len(segments) > RECENT_SEGMENT_LIMIT
        or not all(_valid_permission_segment(segment) for segment in segments)
    ):
        raise _Rejected("permission-transition-invalid")
    rollup = payload.get("activity_rollup")
    if rollup is not None and not _valid_permission_rollup(rollup):
        raise _Rejected("permission-transition-invalid")


def _validate_reactivation_audit(state: MissionState, command: Reactivate) -> None:
    payload = command.compatibility.upserts.thaw()
    if "reactivation_history" not in payload:
        return
    history = payload["reactivation_history"]
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        raise _Rejected("reactivation-audit-invalid")
    previous = []
    previous_category: object = (
        state.control.halt_category.value
        if state.control.halt_category is not None
        else None
    )
    if state.legacy_passthrough is not None:
        passthrough = state.legacy_passthrough.thaw()
        raw_previous = passthrough.get("reactivation_history", [])
        if not isinstance(raw_previous, list):
            raise _Rejected("reactivation-audit-invalid")
        previous = raw_previous
        previous_category = passthrough.get("halt_category")
    if history[:-1] != previous:
        raise _Rejected("reactivation-audit-invalid")
    expected = {
        "timestamp": command.at,
        "previous_halt_reason": state.control.halt_reason,
        "previous_halt_category": previous_category,
        "previous_phase": state.control.phase.value,
        "approved_reason": command.reason,
        "approved_by_user": True,
        "target_phase": command.target.value,
    }
    if history[-1] != expected:
        raise _Rejected("reactivation-audit-invalid")


def _apply_compatibility(
    state: MissionState,
    command_type: Type[object],
    payload: CompatibilityPayload,
    *,
    at: Optional[str] = None,
    dedicated_upserts: Optional[dict[str, object]] = None,
) -> MissionState:
    if not isinstance(payload, CompatibilityPayload):
        raise _Rejected("compatibility-payload-invalid")
    pairs = payload.upserts.items
    keys = [key for key, _value in pairs]
    removals = payload.removals
    if (
        any(not isinstance(key, str) or not key for key in keys)
        or len(keys) != len(set(keys))
        or type(removals) is not tuple
        or any(not isinstance(key, str) or not key for key in removals)
        or len(removals) != len(set(removals))
    ):
        raise _Rejected("compatibility-payload-invalid")
    requested = set(keys) | set(removals)
    if set(keys) & set(removals):
        raise _Rejected("compatibility-field-overlap")
    if requested & (_COMPATIBILITY_FORBIDDEN_FIELDS - _METADATA_FIELDS):
        raise _Rejected("compatibility-field-forbidden")
    allowed = _COMPATIBILITY_FIELDS[command_type]
    if requested - allowed:
        raise _Rejected("compatibility-field-unknown")
    if state.legacy_passthrough is not None:
        existing = set(dict(state.legacy_passthrough.items))
        if requested & _METADATA_FIELDS & existing:
            raise _Rejected("metadata-field-present")
    if at is not None and (not isinstance(at, str) or not at):
        raise _Rejected("invalid-command-time")

    updates = dict(pairs)
    if at is not None:
        updates["updated_at"] = at
    if dedicated_upserts:
        updates.update(dedicated_upserts)
    extensions = dict(state.extensions.items)
    extensions.update(updates)
    for key in removals:
        extensions.pop(key, None)
    changes: dict[str, Any] = {
        "extensions": FrozenJsonObject(tuple(extensions.items()))
    }
    if "session_id" in updates and state.identity.session_id is None:
        session_id = updates["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise _Rejected("metadata-field-invalid")
        changes["identity"] = replace(state.identity, session_id=session_id)
    if state.legacy_passthrough is not None:
        document = dict(state.legacy_passthrough.items)
        document.update(updates)
        for key in removals:
            document.pop(key, None)
        changes["legacy_passthrough"] = FrozenJsonObject(tuple(document.items()))
    next_state = _unbound_state(state, **changes)
    if requested & _A4_OWNED_LEGACY_FIELDS:
        next_state = _sync_a4_projection(next_state)
    return next_state


def _merge_extension_fields(
    state: MissionState,
    fields: FrozenJsonObject,
    *,
    require_nonempty: bool,
) -> MissionState:
    if not isinstance(fields, FrozenJsonObject) or (require_nonempty and not fields.items):
        raise _Rejected("invalid-set-fields")
    keys = [key for key, _value in fields.items]
    if (
        any(not isinstance(key, str) or not key for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise _Rejected("invalid-set-fields")
    requested = set(keys)
    if any(key.startswith("budget_ledger.") for key in requested):
        raise _Rejected("budget-policy-frozen")
    nested = fields.thaw().get("extensions")
    if isinstance(nested, dict) and "budget_ledger" in nested:
        raise _Rejected("budget-policy-frozen")
    if state.budget.policy is not None and "budget_minutes" in requested:
        raise _Rejected("budget-policy-frozen")
    if requested & GENERIC_SET_FROZEN_FIELDS:
        raise _Rejected("frozen-field")
    if requested & GENERIC_SET_DEDICATED_FIELDS:
        raise _Rejected("dedicated-field")
    extensions = dict(state.extensions.items)
    extensions.update(fields.items)
    changes: dict[str, Any] = {
        "extensions": FrozenJsonObject(tuple(extensions.items()))
    }
    if "reviewer_count" in requested:
        reviewer_count = fields.thaw()["reviewer_count"]
        if type(reviewer_count) is not int or reviewer_count < 1:
            raise _Rejected("invalid-set-fields")
        changes["control"] = replace(
            state.control, reviewer_count=reviewer_count
        )
    if state.legacy_passthrough is not None:
        document = dict(state.legacy_passthrough.items)
        document.update(fields.items)
        changes["legacy_passthrough"] = FrozenJsonObject(tuple(document.items()))
    next_state = _unbound_state(state, **changes)
    if requested & _A4_OWNED_LEGACY_FIELDS:
        next_state = _sync_a4_projection(next_state)
    return next_state


# #767 D2.  The one place that decides whether an existing handoff may be
# dropped so the mission can enter executing again.  Both the kernel transition
# and the application layer's early refusal read this, so the two cannot answer
# differently (D4).
#
# The table is keyed by status because that is what the recorded state carries.
# ``prepared`` is split by whether any step was recorded: ``record-step`` does
# not require ``consuming``, so a prepared handoff can already hold completed
# work, and replacing it would lose that record.
_HANDOFF_DISCARD_REFUSALS = {
    "absent": None,
    "consumed": None,
    "rejected": None,
    "consuming": "handoff-in-flight",
}
_HANDOFF_STATUS_UNKNOWN = "handoff-status-unknown"
_HANDOFF_DECISIONS_UNKNOWN = "handoff-decisions-unknown"
_HANDOFF_HAS_RECORDED_STEPS = "handoff-has-recorded-steps"


def handoff_status_name(handoff: object) -> object:
    """Return the recorded status of a handoff as the table spells it.

    ``AbsentHandoff`` carries a plain string while the others carry the enum,
    so the two are normalised here rather than at each call site.
    """
    kind = getattr(handoff, "kind", None)
    return getattr(kind, "value", kind)


def handoff_discard_refusal(status: object, recorded_steps: object) -> str | None:
    """Return the code refusing to discard this handoff, or ``None`` if it may go.

    Unknown statuses are refused rather than treated as "not consuming".  A
    table that answers by exclusion would silently admit any status added
    later, which is the opposite of what this gate is for (D4).
    """
    # ``bool`` is an ``int``, but a truth value is not a count of anything; a
    # ``True`` read as 1 would refuse a clean handoff and ``False`` as 0 would
    # discard one holding completed work.
    if type(recorded_steps) is not int or recorded_steps < 0:
        return _HANDOFF_DECISIONS_UNKNOWN
    if status == "prepared":
        return _HANDOFF_HAS_RECORDED_STEPS if recorded_steps else None
    if not isinstance(status, str) or status not in _HANDOFF_DISCARD_REFUSALS:
        return _HANDOFF_STATUS_UNKNOWN
    return _HANDOFF_DISCARD_REFUSALS[status]


def _advance(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, AdvancePhase)
    control = _active_control(state)
    if command.target not in {Phase.EXECUTING, Phase.REVIEWING}:
        raise _Rejected("terminal-target-forbidden")
    expected = {
        Phase.PLANNING: Phase.EXECUTING,
        Phase.EXECUTING: Phase.REVIEWING,
    }.get(control.phase)
    if command.target is not expected:
        raise _Rejected("invalid-phase-transition")
    if command.target is Phase.EXECUTING and isinstance(state.plan, AbsentPlan):
        raise _Rejected("canonical-plan-required")
    new_handoff = state.handoff
    if command.target is Phase.EXECUTING:
        # #767 D2.  A handoff left over from the previous iteration no longer
        # blocks re-entry on its own; what matters is whether dropping it would
        # lose work.  ``absent`` answers ``None`` here, so the first entry is
        # unchanged.
        refusal = handoff_discard_refusal(
            handoff_status_name(state.handoff),
            len(state.a4.current_handoff_decisions),
        )
        if refusal is not None:
            raise _Rejected(refusal)
        if not isinstance(command.prepared_handoff, PreparedHandoff):
            raise _Rejected("prepared-handoff-required")
        if command.prepared_handoff.plan != state.plan:
            raise _Rejected("handoff-plan-mismatch")
        handoff_token = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}\Z")
        if (
            command.prepared_handoff.schema != "mission-handoff/1"
            or handoff_token.fullmatch(command.prepared_handoff.handoff_id) is None
            or not command.prepared_handoff.ordered_step_ids
            or len(set(command.prepared_handoff.ordered_step_ids))
            != len(command.prepared_handoff.ordered_step_ids)
            or any(
                handoff_token.fullmatch(step_id) is None
                for step_id in command.prepared_handoff.ordered_step_ids
            )
        ):
            raise _Rejected("invalid-prepared-handoff")
        historical_decisions = _a4_authority_document(state).get("decisions", [])
        if any(
            isinstance(item, Mapping)
            and item.get("handoff_id") == command.prepared_handoff.handoff_id
            for item in historical_decisions
        ):
            raise _Rejected("handoff-id-collision")
        new_handoff = command.prepared_handoff
    elif command.prepared_handoff is not None:
        raise _Rejected("unexpected-prepared-handoff")
    new_control = replace(control, phase=command.target)
    new_a4 = state.a4
    if new_handoff is not state.handoff:
        # #767.  The projection keeps every decision whose handoff id is not the
        # new one as history, then writes the current set after it.  Carrying the
        # previous handoff's decisions forward would therefore persist each of
        # them twice -- once as history, once as current.  A replacement starts
        # from none recorded.
        new_a4 = replace(new_a4, current_handoff_decisions=())
    new_state = _unbound_state(
            state,
            control=new_control,
            handoff=new_handoff,
            a4=new_a4,
        )
    new_state = _apply_compatibility(
        new_state,
        AdvancePhase,
        command.compatibility,
        at=command.at,
        dedicated_upserts={"phase": command.target.value},
    )
    return Transition(
        new_state,
        (KernelEvent("phase-advanced"),),
    )


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 2048:
        raise _Rejected("invalid-reason")
    return value


def _mark_halt(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, MarkHalt)
    _validate_permission_compatibility(command)
    if type(command.superseded) is not bool:
        raise _Rejected("invalid-supersede-marker")
    control = _active_control(state)
    if not isinstance(command.category, HaltCategory):
        raise _Rejected("unknown-halt-category")
    reason = _reason(command.reason)
    legacy_reason = command.legacy_reason
    if legacy_reason is None:
        legacy_reason = reason
    if not isinstance(legacy_reason, str) or not legacy_reason.strip():
        raise _Rejected("invalid-legacy-reason")
    if legacy_reason.strip() != reason:
        raise _Rejected("legacy-reason-mismatch")
    outcome = TerminalOutcome(
        terminal_outcome_for_halt(
            command.category.value,
            control.session_role.value,
            superseded=command.superseded,
        )
    )
    new_control = replace(
        control,
        phase=Phase.HALTED,
        terminal_outcome=outcome,
        loop_active=False,
        halt_reason=legacy_reason,
        halt_category=command.category,
    )
    new_state = _merge_extension_fields(
        _unbound_state(state, control=new_control),
        command.extension_fields,
        require_nonempty=False,
    )
    new_state = _apply_compatibility(
        new_state,
        MarkHalt,
        command.compatibility,
        at=command.at,
        dedicated_upserts={
            "phase": Phase.HALTED.value,
            "loop_active": False,
            "halt_reason": legacy_reason,
        },
    )
    if state.budget.policy is not None:
        new_state = _observe_budget(new_state, _budget_at(command), active=False)
    return Transition(
        new_state,
        (KernelEvent("mission-halted"),),
    )


def _reactivation_target(value: object) -> Phase:
    if value not in {Phase.PLANNING, Phase.EXECUTING, Phase.REVIEWING, Phase.SCORING}:
        raise _Rejected("invalid-reactivation-target")
    assert isinstance(value, Phase)
    return value


def _is_stale_halt(state: MissionState) -> bool:
    return (
        state.control.halt_category is HaltCategory.STALE
        or state.terminal_outcome is TerminalOutcome.STALE_SUPERSEDED
    )


def _reactivate(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, Reactivate)
    control = state.control
    if state.budget.policy is not None:
        at = _budget_at(command)
        from .budget import exhaustion
        if _budget_guard(exhaustion, state.budget, at) is not None:
            raise _Rejected('budget-exhausted')
    if command.approved_by_user is not True:
        raise _Rejected("approval-required")
    _reason(command.reason)
    if control.passes or control.phase is not Phase.HALTED or control.loop_active is not False or not control.halt_reason:
        raise _Rejected("manual-halt-required")
    if _is_stale_halt(state):
        raise _Rejected("stale-requires-resume")
    if control.halt_category is not command.expected_category:
        raise _Rejected("halt-category-mismatch")
    target = _reactivation_target(command.target)
    _validate_reactivation_audit(state, command)
    new_control = replace(
        control,
        phase=target,
        terminal_outcome=None,
        loop_active=True,
        halt_reason="",
        halt_category=None,
    )
    new_state = _apply_compatibility(
        _unbound_state(state, control=new_control),
        Reactivate,
        command.compatibility,
        at=command.at,
        dedicated_upserts={
            "phase": target.value,
            "loop_active": True,
            "halt_reason": "",
        },
    )
    if state.budget.policy is not None:
        new_state = _observe_budget(new_state, _budget_at(command), active=True)
    return Transition(
        new_state,
        (KernelEvent("mission-reactivated"),),
    )


def _resume_stale(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, ResumeStale)
    control = state.control
    if (
        control.phase is not Phase.HALTED
        or control.loop_active is not False
        or not control.halt_reason
        or not _is_stale_halt(state)
    ):
        raise _Rejected("stale-halt-required")
    target = _reactivation_target(command.target)
    if command.new_pid is not None and (
        type(command.new_pid) is not int or command.new_pid <= 0
    ):
        raise _Rejected("invalid-new-pid")
    new_control = replace(
        control,
        phase=target,
        terminal_outcome=None,
        loop_active=True,
        halt_reason="",
        halt_category=None,
    )
    dedicated = {
        "phase": target.value,
        "loop_active": True,
        "halt_reason": "",
    }
    if command.new_pid is not None:
        dedicated["pid"] = command.new_pid
    new_state = _apply_compatibility(
        _unbound_state(state, control=new_control),
        ResumeStale,
        command.compatibility,
        at=command.at,
        dedicated_upserts=dedicated,
    )
    if state.budget.policy is not None:
        new_state = _observe_budget(new_state, _budget_at(command), active=True)
    return Transition(
        new_state,
        (KernelEvent("stale-mission-resumed"),),
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _latest_declared_score(state: MissionState) -> tuple[BoundScore | None, dict[str, object] | None]:
    for score in reversed(state.scores):
        payload = score.payload.thaw()
        if "composite" not in payload:
            continue
        if not isinstance(score, BoundScore) or score.authoritative is not True:
            return None, payload
        return score, payload
    return None, None


def _maximum_agreement_delta(payload: dict[str, object]) -> float | None:
    detail = payload.get("agreement_detail")
    if not isinstance(detail, dict):
        return None
    maximum = None
    for raw in detail.values():
        if not isinstance(raw, dict):
            continue
        delta = _finite_number(raw.get("delta"))
        if delta is not None and (maximum is None or delta > maximum):
            maximum = delta
    return maximum


def _acceptance_completion_ready(state: MissionState, command: MarkPass) -> None:
    """Keep contract-enabled sessions pending until typed coverage exists."""
    document = (
        state.legacy_passthrough.thaw()
        if state.legacy_passthrough is not None
        else state.extensions.thaw()
    )
    if "acceptance_contract" not in document:
        return
    contract = document["acceptance_contract"]
    if not isinstance(contract, dict):
        raise _Rejected("acceptance-contract-invalid")
    from acceptance_contract import AcceptanceContractError, frozen_verifier_commands
    try:
        commands = frozen_verifier_commands(contract)
    except AcceptanceContractError as exc:
        raise _Rejected(str(exc)) from exc
    if contract.get("coverage") != {"status": "valid"}:
        raise _Rejected("acceptance-coverage-pending")
    criteria = contract.get("criteria")
    if not isinstance(criteria, list):
        raise _Rejected("acceptance-contract-invalid")
    required = []
    for item in criteria:
        if not isinstance(item, dict) or type(item.get("required")) is not bool or not isinstance(item.get("id"), str) or not item["id"]:
            raise _Rejected("acceptance-contract-invalid")
        if item["required"]:
            required.append(item)
    if not required:
        raise _Rejected("acceptance-contract-invalid")
    history = document.get("verification_receipts")
    if not isinstance(history, list):
        raise _Rejected("acceptance-receipt-missing")
    if any(not isinstance(item, dict) or not isinstance(item.get("criterion_id"), str) or not isinstance(item.get("status"), str) for item in history):
        raise _Rejected("acceptance-receipt-invalid")
    policy = contract.get("verifier_policy")
    policy_digest = policy.get("digest") if isinstance(policy, dict) else None
    if not isinstance(commands, dict) or not isinstance(policy_digest, str):
        raise _Rejected("acceptance-contract-invalid")
    try:
        from acceptance_contract import canonical_contract_digest, verifier_definition_digest
        contract_digest = canonical_contract_digest(contract)
    except (TypeError, ValueError):
        raise _Rejected("acceptance-contract-invalid")
    try:
        candidates = command.acceptance_candidate_digests.thaw()
    except AttributeError:
        raise _Rejected("acceptance-candidate-invalid")
    if not isinstance(candidates, dict) or set(candidates) != {item["id"] for item in required} or not all(isinstance(value, str) for value in candidates.values()):
        raise _Rejected("acceptance-candidate-missing")
    for criterion in required:
        criterion_id = criterion["id"]
        command_id = criterion.get("command_id")
        verifier_command = commands.get(command_id) if isinstance(command_id, str) else None
        if not isinstance(verifier_command, dict):
            raise _Rejected("acceptance-contract-invalid")
        latest = next((item for item in reversed(history) if item["criterion_id"] == criterion_id), None)
        if latest is None:
            raise _Rejected("acceptance-receipt-missing")
        if latest.get("status") != "passed":
            raise _Rejected("acceptance-receipt-not-passed")
        if (latest.get("contract_digest") != contract_digest or latest.get("verifier_policy_digest") != policy_digest or latest.get("verifier_definition_digest") != verifier_definition_digest(verifier_command)):
            raise _Rejected("acceptance-receipt-stale")
        if latest.get("candidate_digest") != candidates[criterion_id]:
            raise _Rejected("acceptance-receipt-stale")
    raise _Rejected("acceptance-fresh-review-pending")


def acceptance_completion_rejection(state: MissionState, command: MarkPass) -> str | None:
    """Preflight the same pure guard before an approval provider can publish.

    This grants no transition authority: MarkPass repeats the guard against
    the repository's authoritative state at the actual completion decision.
    """
    try:
        _acceptance_completion_ready(state, command)
    except _Rejected as exc:
        return exc.code
    return None


def _mark_pass(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, MarkPass)
    if state.budget.policy is not None:
        reason = _budget_guard(completion_rejection, state.budget, _budget_at(command))
        if reason is not None:
            raise _Rejected(reason)
    control = _active_control(state)
    _acceptance_completion_ready(state, command)
    if type(command.force) is not bool:
        raise _Rejected("invalid-force-flag")
    if command.force:
        if command.force_approval_verified is not True:
            raise _Rejected("force-approval-required")
    else:
        if command.verified_score_index is not None:
            index = command.verified_score_index
            if (
                type(index) is not int
                or index < 0
                or index >= len(state.scores)
                or not isinstance(state.scores[index], BoundScore)
            ):
                raise _Rejected("authoritative-score-required")
            score = state.scores[index]
            assert isinstance(score, BoundScore)
            state = _unbound_state(
                state,
                scores=(*state.scores[:index], replace(score, authoritative=True), *state.scores[index + 1 :]),
            )
        _score, payload = _latest_declared_score(state)
        if payload is None:
            raise _Rejected("score-required")
        if _score is None:
            raise _Rejected("authoritative-score-required")
        open_high = payload.get("open_high", 0)
        if isinstance(open_high, bool) or not isinstance(open_high, int) or open_high < 0:
            raise _Rejected("invalid-open-high")
        if open_high > 0:
            raise _Rejected("open-high-findings")
        composite = _finite_number(payload.get("composite"))
        threshold = 4.0 if control.threshold is None else _finite_number(control.threshold)
        if threshold is None or composite is None or composite < threshold:
            raise _Rejected("composite-below-threshold")
        minimum = _finite_number(payload.get("min_item"))
        if minimum is None or minimum < 3.5:
            raise _Rejected("minimum-item-below-threshold")
        agreement_delta = _maximum_agreement_delta(payload)
        if agreement_delta is not None and agreement_delta > 1.5:
            raise _Rejected("review-agreement-too-low")
        if command.specialist_gate_satisfied is not True:
            raise _Rejected("specialist-gate-unsatisfied")
    if command.artifact_gate_satisfied is not True:
        raise _Rejected("artifact-gate-unsatisfied")
    new_control = replace(
        control,
        phase=Phase.DONE,
        terminal_outcome=TerminalOutcome.COMPLETED_PASS,
        loop_active=False,
        passes=True,
    )
    new_state = _apply_compatibility(
        _unbound_state(state, control=new_control),
        MarkPass,
        command.compatibility,
        at=command.at,
        dedicated_upserts={
            "phase": Phase.DONE.value,
            "passes": True,
            "loop_active": False,
        },
    )
    if state.budget.policy is not None:
        new_state = _observe_budget(new_state, _budget_at(command), active=False)
    if command.force:
        force_payload = command.compatibility.upserts.thaw().get("force_approval")
        if not isinstance(force_payload, dict) or force_payload.get("consumed") is not True:
            raise _Rejected("force-approval-binding-invalid")
        request = force_payload.get("request") if isinstance(force_payload, dict) else None
        expected_digest = (
            request.get("terminal_object_digest") if isinstance(request, dict) else None
        )
        try:
            from .codec_v4 import project_legacy_document
            from scoring_provenance import terminal_state_digest

            actual_digest = terminal_state_digest(
                json.loads(project_legacy_document(new_state))
            )
        except (TypeError, ValueError, UnicodeError):
            raise _Rejected("force-approval-binding-invalid")
        if expected_digest != actual_digest:
            raise _Rejected("force-approval-binding-invalid")
    return Transition(
        new_state,
        (KernelEvent("mission-passed"),),
    )


def _set_extension_fields(state: MissionState, raw_command: object) -> Transition:
    """Merge generic extension properties under the closed field authority.

    Administrative property writes remain legal in halted states (parity with
    the v4 ``set`` adapter), so this reducer deliberately does not require an
    active control.  Completion-adjacent authority is protected by the closed
    frozen/dedicated field classification instead.
    """
    command = raw_command
    assert isinstance(command, SetExtensionFields)
    merged = _merge_extension_fields(state, command.fields, require_nonempty=True)
    new_state = _apply_compatibility(
        merged,
        SetExtensionFields,
        command.compatibility,
        at=command.at,
    )
    return Transition(
        new_state,
        (KernelEvent("extension-fields-set"),),
    )


def _decline_specialist_selection(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, DeclineSpecialistSelection)
    _active_control(state)
    document = _artifact_document(state)
    checkpoint = document.get("specialists_decision")
    if not isinstance(checkpoint, dict):
        raise _Rejected("specialist-selection-checkpoint-invalid")
    if checkpoint.get("selection_id") != command.selection_id:
        raise _Rejected("specialist-selection-id-mismatch")
    if (
        checkpoint.get("decision") != "none"
        or checkpoint.get("lifecycle_state") != "candidate"
        or checkpoint.get("reason_code") != "awaiting-confirmation"
    ):
        raise _Rejected("specialist-selection-not-declinable")
    declined = {
        **checkpoint,
        "action": "continue-core",
        "decision": "declined",
        "reason": command.reason.strip(),
        "reason_code": "orchestrator-declined",
        "prompted_user": False,
        "lifecycle_state": "terminal",
    }
    document["specialists_decision"] = declined
    document["specialists_mode"] = "manual"
    if command.at is not None:
        document["updated_at"] = command.at
    # #624 で specialists_decision は typed A4 projection 側が権威になった。
    # legacy document だけを書くと projection に上書きされて無効化される。
    frozen_decision = freeze_json_value(declined)
    if not isinstance(frozen_decision, FrozenJsonObject):
        raise _Rejected("specialist-selection-checkpoint-invalid")
    next_state = _with_artifact_document(state, document)
    next_state = _unbound_state(
        next_state,
        a4=replace(
            next_state.a4,
            specialist_selection=replace(
                next_state.a4.specialist_selection,
                decision=frozen_decision,
                mode="manual",
            ),
        ),
    )
    return Transition(
        next_state,
        (KernelEvent("specialist-selection-declined"),),
    )


def _artifact_document(state: MissionState) -> dict:
    if state.legacy_passthrough is None:
        raise _Rejected("artifact-v4-state-required")
    return state.legacy_passthrough.thaw()


def _with_artifact_document(state: MissionState, document: dict) -> MissionState:
    frozen = freeze_json_value(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise _Rejected("artifact-state-invalid")
    return _unbound_state(state, legacy_passthrough=frozen)


def _artifact_transition(
    state: MissionState,
    project: Callable[[dict], dict],
    *,
    event: str,
    effects: tuple[object, ...],
) -> Transition:
    try:
        document = project(_artifact_document(state))
    except ArtifactRuleError as rejected:
        raise _Rejected(rejected.code)
    return Transition(
        _with_artifact_document(state, document),
        (KernelEvent(event),),
        effects,
    )


def _initialize_artifact(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, InitializeArtifact)
    return _artifact_transition(
        state,
        lambda document: initialize_artifact_document(
            document,
            at=command.at,
            path=command.path,
            format=command.format,
            title=command.title,
            redaction_status=command.redaction_status,
            required_for_pass=command.required_for_pass,
            effect=command.effect,
        ),
        event="artifact-initialized",
        effects=(command.effect,),
    )


def _append_artifact_block(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, AppendArtifactBlock)
    return _artifact_transition(
        state,
        lambda document: append_artifact_block_document(
            document,
            at=command.at,
            section=command.section,
            content=command.content,
            source=command.source,
            label=command.label,
        ),
        event="artifact-block-appended",
        effects=(),
    )


def _render_artifact(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, RenderArtifact)
    return _artifact_transition(
        state,
        lambda document: render_artifact_document(
            document,
            at=command.at,
            redaction_status=command.redaction_status,
            effect=command.effect,
        ),
        event="artifact-rendered",
        effects=(command.effect,),
    )


def _export_artifact(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, ExportArtifact)
    return _artifact_transition(
        state,
        lambda document: export_artifact_document(
            document,
            at=command.at,
            destination=command.destination,
            redaction_status=command.redaction_status,
            artifact_effect=command.artifact_effect,
            export_effect=command.export_effect,
        ),
        event="artifact-exported",
        effects=(command.artifact_effect, command.export_effect),
    )


def _record_artifact_publication(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, RecordArtifactPublication)
    return _artifact_transition(
        state,
        lambda document: record_artifact_publication_document(
            document,
            at=command.at,
            provider=command.provider,
            destination=command.destination,
            approval_text=command.approval_text,
            confirmed=command.confirmed,
            effect=command.effect,
        ),
        event="artifact-publication-recorded",
        effects=(command.effect,),
    )


def _evidence_document(state: MissionState) -> dict:
    if state.legacy_passthrough is None:
        raise _Rejected("evidence-v4-state-required")
    return state.legacy_passthrough.thaw()


def _with_evidence_document(state: MissionState, document: dict) -> MissionState:
    frozen = freeze_json_value(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise _Rejected("evidence-state-invalid")
    return _unbound_state(state, legacy_passthrough=frozen)


def _claim_identity_matches(claim: object, content: bytes) -> bool:
    import hashlib

    return (
        isinstance(getattr(claim, "kind", None), str)
        and isinstance(getattr(claim, "target", None), str)
        and type(getattr(claim, "size", None)) is int
        and claim.size == len(content)
        and claim.digest == "sha256:" + hashlib.sha256(content).hexdigest()
    )


def _update_progress(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, UpdateProgress)
    if type(command.effect) is not ProgressEffectClaim or command.effect.kind != "progress":
        raise _Rejected("progress-effect-claim-invalid")
    try:
        document, content = apply_progress_update(_evidence_document(state), command)
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    if not _claim_identity_matches(command.effect, content):
        raise _Rejected("progress-effect-claim-invalid")
    return Transition(
        _with_evidence_document(state, document),
        (KernelEvent("progress-checkpoint-updated"),),
        (command.effect,),
    )


def _clear_progress(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, ClearProgress)
    try:
        document = apply_progress_clear(_evidence_document(state), command)
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    return Transition(
        _with_evidence_document(state, document),
        (KernelEvent("progress-checkpoint-cleared"),),
    )


def _generate_context_manifest(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, GenerateContextManifest)
    claim = command.effect
    if (
        type(claim) is not ContextManifestEffectClaim
        or claim.kind != "context-manifest"
        or not isinstance(claim.publication_path, str)
        or Path(claim.publication_path).name != claim.target
    ):
        raise _Rejected("context-effect-claim-invalid")
    try:
        document, content, _count = apply_context_manifest(
            _evidence_document(state), command
        )
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    if (
        document["context_manifests"][str(command.iteration)]["digest"]
        != claim.digest
        or not _claim_identity_matches(claim, content)
    ):
        raise _Rejected("context-effect-claim-invalid")
    return Transition(
        _with_evidence_document(state, document),
        (KernelEvent("context-manifest-recorded"),),
        (claim,),
    )


def _record_verification(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, RecordVerification)
    try:
        document, entry = apply_verification_record(
            _evidence_document(state), command
        )
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    return Transition(
        _with_evidence_document(state, document),
        (KernelEvent("verification-recorded"),),
    )


def _record_verification_receipt(state: MissionState, raw_command: object) -> Transition:
    from .budget_decisions import verification_result_digest, stalled_candidate, verification_refusal_reason
    command = raw_command
    assert isinstance(command, RecordVerificationReceipt)
    if state.budget.policy is not None and command.settlement is None:
        raise _Rejected('verification-settlement-binding-invalid')
    if command.settlement is not None:
        row = next((r for r in state.budget.reservations if r.reservation_id == command.settlement.reservation_id), None)
        receipt = command.receipt.thaw()
        if (row is None or row.entry != 'verification-run' or row.target != receipt.get('criterion_id')
                or command.settlement.at != command.at
                or command.settlement.result_digest != verification_result_digest(receipt)
                or command.settlement.candidate_digest != receipt.get('candidate_digest')
                or command.settlement.output_bytes != receipt.get('observed_output_bytes')
                or command.settlement.completed != (receipt.get('status') in ('passed', 'failed'))
                or command.settlement.refusal_reason != verification_refusal_reason(receipt)
                or receipt.get('block_reason') == 'budget-no-new-evidence' and stalled_candidate(
                    state.budget, 'verification-run', row.target) != receipt.get('candidate_digest')
                or (command.settlement.outcome == 'kill-unconfirmed') != (receipt.get('block_reason') == 'kill-unconfirmed')):
            raise _Rejected('verification-settlement-binding-invalid')
        state = _settle_budget(state, command.settlement).new_state
    try:
        document, _entry = apply_verification_receipt(_evidence_document(state), command)
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    return Transition(_with_evidence_document(state, document), (KernelEvent("verification-receipt-recorded"),))


def _import_acceptance_contract(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, ImportAcceptanceContract)
    from .evidence import apply_acceptance_contract, EvidenceRuleError
    try:
        document, _entry = apply_acceptance_contract(_evidence_document(state), command)
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    return Transition(_with_evidence_document(state, document), (KernelEvent("acceptance-contract-imported"),))


def _prepare_fresh_review(state: MissionState, command: object) -> Transition:
    from .fresh_review import prepare_request_state, FreshReviewError
    try:
        next_state = prepare_request_state(state, command)
    except FreshReviewError as rejected:
        raise _Rejected(rejected.code)
    return Transition(next_state, (KernelEvent("fresh-review-prepared"),))


def _fresh_review_dispatch(state: MissionState, command: object) -> Transition:
    from .fresh_review_dispatch import dispatch_state
    from .fresh_review import FreshReviewError
    try:
        next_state = dispatch_state(state, command)
    except FreshReviewError as rejected:
        raise _Rejected(rejected.code)
    return Transition(next_state, (KernelEvent("fresh-review-dispatch-recorded"),))


def _generate_claims_ledger(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, GenerateClaimsLedger)
    claim = command.effect
    if type(claim) is not ClaimsLedgerEffectClaim or claim.kind != "claims-ledger":
        raise _Rejected("claims-ledger-effect-claim-invalid")
    try:
        document = apply_claims_ledger(_evidence_document(state), command)
    except EvidenceRuleError as rejected:
        raise _Rejected(rejected.code)
    # The adapter verifies actual bytes before committing state. The kernel owns
    # the closed descriptor and cannot derive a Git-backed ledger itself.
    if type(claim.size) is not int or claim.size < 0 or not isinstance(claim.digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", claim.digest):
        raise _Rejected("claims-ledger-effect-claim-invalid")
    return Transition(_with_evidence_document(state, document), (KernelEvent("claims-ledger-recorded"),), (claim,))


def _handoff_command_facts(
    state: MissionState, observation: object
) -> tuple[object, dict[str, tuple[str, ...]]]:
    if not isinstance(observation, CanonicalPlanObservation):
        raise _Rejected("canonical-plan-observation-invalid")
    if (
        not isinstance(observation.path, str)
        or not observation.path
        or not isinstance(observation.digest, str)
        or not observation.digest
        or type(observation.generation) is not int
        or observation.generation < 1
        or observation.source not in {"core", "provider"}
        or not isinstance(observation.source_id, str)
        or not observation.source_id
        or not isinstance(observation.selection_source, str)
        or not observation.selection_source
        or type(observation.iteration) is not int
        or observation.iteration < 0
        or type(observation.ordered_step_ids) is not tuple
        or not observation.ordered_step_ids
        or any(
            not isinstance(step, str) or not step
            for step in observation.ordered_step_ids
        )
        or len(set(observation.ordered_step_ids))
        != len(observation.ordered_step_ids)
        or type(observation.dependencies) is not tuple
        or not isinstance(observation.raw, bytes)
    ):
        raise _Rejected("canonical-plan-observation-invalid")
    handoff = state.handoff
    if not isinstance(handoff, (PreparedHandoff, ConsumingHandoff)):
        raise _Rejected("executor-handoff-not-active")
    plan = state.plan
    source = getattr(getattr(plan, "source", None), "value", None)
    if (
        getattr(plan, "path", None) != observation.path
        or getattr(plan, "digest", None) != observation.digest
        or getattr(plan, "generation", None) != observation.generation
        or source != observation.source
        or getattr(plan, "source_id", None) != observation.source_id
        or getattr(plan, "selection_source", None)
        != observation.selection_source
        or getattr(plan, "iteration", None) != observation.iteration
        or observation.iteration != state.control.iteration
        or handoff.plan != plan
        or handoff.ordered_step_ids != observation.ordered_step_ids
    ):
        raise _Rejected("executor-handoff-plan-drift")
    for item in observation.dependencies:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or type(item[1]) is not tuple
            or any(not isinstance(value, str) for value in item[1])
        ):
            raise _Rejected("executor-handoff-dependencies-invalid")
    try:
        actual_digest = "sha256:" + hashlib.sha256(observation.raw).hexdigest()
        payload = json.loads(observation.raw.decode("utf-8"))
        if canonical_plan_bytes(payload) != observation.raw:
            raise ValueError("noncanonical")
    except (UnicodeDecodeError, json.JSONDecodeError, PlanContractError, ValueError):
        raise _Rejected("executor-handoff-plan-drift") from None
    if (
        actual_digest != observation.digest
        or not isinstance(payload, dict)
        or payload.get("schema") != "mission-plan/1"
        or not isinstance(payload.get("steps"), list)
        or not payload["steps"]
    ):
        raise _Rejected("executor-handoff-plan-drift")
    derived_ids: list[str] = []
    derived_dependencies: list[tuple[str, tuple[str, ...]]] = []
    for item in payload["steps"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or not isinstance(item.get("depends_on"), list)
            or any(not isinstance(value, str) or not value for value in item["depends_on"])
        ):
            raise _Rejected("executor-handoff-plan-drift")
        derived_ids.append(item["id"])
        derived_dependencies.append((item["id"], tuple(item["depends_on"])))
    if len(set(derived_ids)) != len(derived_ids):
        raise _Rejected("executor-handoff-plan-drift")
    known_derived = set(derived_ids)
    if any(
        len(set(values)) != len(values)
        or any(value not in known_derived for value in values)
        for _, values in derived_dependencies
    ):
        raise _Rejected("executor-handoff-plan-drift")
    if (
        tuple(derived_ids) != observation.ordered_step_ids
        or tuple(derived_dependencies) != observation.dependencies
    ):
        raise _Rejected("executor-handoff-plan-drift")
    dependency_keys = tuple(
        item[0]
        for item in observation.dependencies
        if isinstance(item, tuple) and len(item) == 2
    )
    if (
        len(dependency_keys) != len(observation.dependencies)
        or dependency_keys != observation.ordered_step_ids
    ):
        raise _Rejected("executor-handoff-dependencies-invalid")
    dependencies: dict[str, tuple[str, ...]] = {}
    known = set(observation.ordered_step_ids)
    for step_id, values in observation.dependencies:
        if (
            type(values) is not tuple
            or any(
                not isinstance(value, str) or value not in known
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            raise _Rejected("executor-handoff-dependencies-invalid")
        dependencies[step_id] = values
    if not isinstance(state.a4, A4Projection):
        raise _Rejected("a4-projection-invalid")
    for item in state.a4.current_handoff_decisions:
        if (
            item.handoff_id != handoff.handoff_id
            or item.plan_digest != observation.digest
            or item.plan_generation != observation.generation
            or item.plan_source != observation.source
            or item.source_id != observation.source_id
            or item.selection_source != observation.selection_source
            or item.iteration != observation.iteration
            or item.step_id not in known
            or item.result not in {"ok", "partial", "failed"}
        ):
            raise _Rejected("executor-handoff-decisions-invalid")
    completed = [item.step_id for item in state.a4.current_handoff_decisions]
    if len(completed) != len(set(completed)):
        raise _Rejected("executor-handoff-decisions-invalid")
    return handoff, dependencies


def _handoff_state(
    state: MissionState,
    *,
    at: object,
    handoff: Optional[object] = None,
    decisions: Optional[tuple[ExecutorStepDecision, ...]] = None,
) -> MissionState:
    if not isinstance(at, str) or not at:
        raise _Rejected("executor-handoff-request-invalid")
    if state.legacy_passthrough is None:
        raise _Rejected("executor-handoff-v4-projection-required")
    document = state.legacy_passthrough.thaw()
    document["updated_at"] = at
    frozen = freeze_json_value(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise _Rejected("executor-handoff-state-invalid")
    a4 = state.a4
    assert isinstance(a4, A4Projection)
    if decisions is not None:
        a4 = replace(a4, current_handoff_decisions=decisions)
    return _unbound_state(
        state,
        handoff=state.handoff if handoff is None else handoff,
        a4=a4,
        legacy_passthrough=frozen,
    )


def _begin_executor_handoff(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, BeginExecutorHandoff)
    handoff, _dependencies = _handoff_command_facts(state, command.plan)
    if not isinstance(handoff, PreparedHandoff):
        raise _Rejected("executor-handoff-not-prepared")
    consuming = ConsumingHandoff(
        handoff.schema,
        handoff.handoff_id,
        handoff.plan,
        handoff.ordered_step_ids,
        command.at,
    )
    return Transition(
        _handoff_state(state, at=command.at, handoff=consuming),
        (KernelEvent("executor-handoff-begun"),),
    )


def _verify_executor_step(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, VerifyExecutorStep)
    handoff, _dependencies = _handoff_command_facts(state, command.plan)
    if command.step_id not in handoff.ordered_step_ids:
        raise _Rejected("executor-step-not-member")
    return Transition(
        _handoff_state(state, at=command.at),
        (KernelEvent("executor-step-revalidated"),),
    )


def _record_executor_step(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, RecordExecutorStep)
    handoff, dependencies = _handoff_command_facts(state, command.plan)
    if command.step_id not in handoff.ordered_step_ids:
        raise _Rejected("executor-step-not-member")
    completed = {item.step_id for item in state.a4.current_handoff_decisions}
    if command.step_id in completed:
        raise _Rejected("executor-step-already-recorded")
    if any(
        dependency not in completed
        for dependency in dependencies[command.step_id]
    ):
        raise _Rejected("executor-step-dependency-incomplete")
    if command.result not in {"ok", "partial", "failed"}:
        raise _Rejected("executor-step-result-invalid")
    appended = ExecutorStepDecision(
        handoff_id=handoff.handoff_id,
        plan_digest=command.plan.digest,
        plan_generation=command.plan.generation,
        plan_source=command.plan.source,
        source_id=command.plan.source_id,
        selection_source=command.plan.selection_source,
        iteration=command.plan.iteration,
        step_id=command.step_id,
        result=command.result,
    )
    return Transition(
        _handoff_state(
            state,
            at=command.at,
            decisions=(*state.a4.current_handoff_decisions, appended),
        ),
        (KernelEvent("executor-step-recorded"),),
    )


def _complete_executor_handoff(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, CompleteExecutorHandoff)
    handoff, _dependencies = _handoff_command_facts(state, command.plan)
    if not isinstance(handoff, ConsumingHandoff):
        raise _Rejected("executor-handoff-not-consuming")
    completed = {item.step_id for item in state.a4.current_handoff_decisions}
    if completed != set(handoff.ordered_step_ids):
        raise _Rejected("executor-handoff-incomplete")
    consumed = ConsumedHandoff(
        handoff.schema,
        handoff.handoff_id,
        handoff.plan,
        handoff.ordered_step_ids,
        handoff.begun_at,
        command.at,
    )
    return Transition(
        _handoff_state(state, at=command.at, handoff=consumed),
        (KernelEvent("executor-handoff-consumed"),),
    )


def _reject_executor_handoff(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, RejectExecutorHandoff)
    handoff = state.handoff
    if isinstance(handoff, AbsentHandoff):
        raise _Rejected("executor-handoff-missing")
    if (
        command.attempted_operation not in {"begin", "verify-step"}
        or not isinstance(command.reason_code, CanonicalPlanRejectionCode)
    ):
        raise _Rejected("executor-handoff-rejection-invalid")
    rejected = RejectedHandoff(
        handoff.schema,
        handoff.handoff_id,
        handoff.plan,
        handoff.ordered_step_ids,
        command.reason_code.value,
        getattr(handoff, "begun_at", None),
    )
    return Transition(
        _handoff_state(state, at=command.at, handoff=rejected),
        (KernelEvent("executor-handoff-rejected"),),
    )


# #767 D3.  ``prepared`` and ``consuming`` are the two statuses that hold work
# open, so they are the two that need a published way out.  The other three are
# terminal: aborting them would report success without changing anything, and a
# second abort would overwrite the first reason.
_ABORTABLE_HANDOFF_STATUSES = (PreparedHandoff, ConsumingHandoff)


def _abort_executor_handoff(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, AbortExecutorHandoff)
    handoff = state.handoff
    if not isinstance(handoff, _ABORTABLE_HANDOFF_STATUSES):
        raise _Rejected("executor-handoff-not-abortable")
    # The reason has to arrive as the enum, not as its value.  Accepting the
    # string would let any caller assembling the command write an arbitrary
    # reason, which is the thing the closed set exists to prevent.
    if not isinstance(command.reason, HandoffAbortReason):
        raise _Rejected("executor-handoff-abort-reason-invalid")
    rejected = RejectedHandoff(
        handoff.schema,
        handoff.handoff_id,
        handoff.plan,
        handoff.ordered_step_ids,
        command.reason.value,
        getattr(handoff, "begun_at", None),
    )
    # Decisions are left alone.  ``prepared`` with decisions is exactly the case
    # D2 refuses to discard on its own; dropping them here would lose the record
    # of completed steps at the moment the operator is told it is safe to move on.
    return Transition(
        _handoff_state(state, at=command.at, handoff=rejected),
        (KernelEvent("executor-handoff-aborted"),),
    )


_SPECIALIST_AUTHORITY_FIELDS = frozenset(
    {
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "terminal_outcome",
        "score_history",
        "review_result",
        "review_findings",
        "provider_output",
        "final_report",
    }
)


def _specialist_record(
    value: object, *, allowed_authority: frozenset[str] = frozenset()
) -> dict:
    if not isinstance(value, FrozenJsonObject):
        raise _Rejected("specialist-recommendation-invalid")
    record = value.thaw()
    if set(record) & (_SPECIALIST_AUTHORITY_FIELDS - allowed_authority):
        raise _Rejected("specialist-recommendation-authority-invalid")
    return record


def _record_specialist_recommendation(
    state: MissionState, raw_command: object
) -> Transition:
    command = raw_command
    assert isinstance(command, RecordSpecialistRecommendation)
    projection = command.projection
    if (
        not isinstance(projection, SpecialistRecommendationProjection)
        or not isinstance(command.at, str)
        or not command.at
        or not (
            command.expected_complexity is None
            or isinstance(command.expected_complexity, str)
        )
        or type(command.expected_iteration) is not int
        or not isinstance(state.a4, A4Projection)
        or state.legacy_passthrough is None
    ):
        raise _Rejected("specialist-recommendation-invalid")
    try:
        validate_specialist_recommendation_shape(projection)
    except A4ProjectionError as exc:
        raise _Rejected(exc.code) from exc
    document = state.legacy_passthrough.thaw()
    if (
        command.expected_iteration != state.control.iteration
        or document.get("complexity") != command.expected_complexity
    ):
        raise _Rejected("specialist-recommendation-context-mismatch")
    current = state.a4.specialist_selection
    if current.active_provider_invocation_ids:
        raise _Rejected("provider-invocation-active")
    task_profile = _specialist_record(projection.task_profile)
    candidates = [_specialist_record(item) for item in projection.candidates]
    selected = [_specialist_record(item) for item in projection.selected]
    unavailable = [_specialist_record(item) for item in projection.unavailable]
    ineligible = [_specialist_record(item) for item in projection.ineligible]
    decision = _specialist_record(projection.decision)
    phase_plan = [
        _specialist_record(item, allowed_authority=frozenset({"phase"}))
        for item in projection.phase_plan
    ]
    try:
        validate_specialist_recommendation_projection(projection)
    except A4ProjectionError as exc:
        raise _Rejected(exc.code) from exc
    except SpecialistPublicContractError as exc:
        raise _Rejected("specialist-selection-invalid") from exc
    selection_id = decision.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise _Rejected("specialist-recommendation-selection-invalid")
    if any(
        item.get("selection_id") != selection_id
        for item in (*candidates, *selected, *unavailable)
    ):
        raise _Rejected("specialist-recommendation-selection-invalid")
    candidate_identities = {
        (item.get("provider_id"), item.get("skill")) for item in candidates
    }
    if any(
        (item.get("provider_id"), item.get("skill"))
        not in candidate_identities
        for item in (*selected, *unavailable)
    ):
        raise _Rejected("specialist-recommendation-selection-invalid")
    decision_kind = decision.get("decision")
    if (decision_kind == "selected") != bool(selected):
        raise _Rejected("specialist-recommendation-selection-invalid")
    planning = next(
        (
            item
            for item in selected
            if item.get("planning_mode") in {"advisory", "primary"}
        ),
        None,
    )
    strategy = current.planning_strategy
    contract_digest = current.planning_contract_digest
    binding = current.planning_provider_binding
    if planning is not None:
        provider_id = planning.get("provider_id")
        planning_digest = planning.get("planning_contract_digest")
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or not isinstance(planning_digest, str)
            or not planning_digest
        ):
            raise _Rejected("specialist-recommendation-planning-invalid")
        strategy = "provider-" + planning["planning_mode"]
        contract_digest = planning_digest
        frozen_binding = freeze_json_value(
            {
                "provider_id": provider_id,
                "selection_id": selection_id,
                "planning_contract_digest": planning_digest,
            }
        )
        assert isinstance(frozen_binding, FrozenJsonObject)
        binding = frozen_binding
    elif current.planning_policy_version == 1:
        strategy = "core"
        binding = None
    next_selection = SpecialistSelectionProjection(
        task_profile=projection.task_profile,
        candidates=projection.candidates,
        selected=projection.selected,
        unavailable=projection.unavailable,
        ineligible=projection.ineligible,
        registry_projection=projection.registry_projection,
        decision=projection.decision,
        phase_plan=projection.phase_plan,
        mode=projection.mode,
        active_provider_invocation_ids=current.active_provider_invocation_ids,
        planning_policy_version=current.planning_policy_version,
        planning_strategy=strategy,
        planning_contract_digest=contract_digest,
        planning_provider_binding=binding,
    )
    document["updated_at"] = command.at
    frozen = freeze_json_value(document)
    if not isinstance(frozen, FrozenJsonObject):
        raise _Rejected("specialist-recommendation-invalid")
    next_state = _unbound_state(
        state,
        a4=replace(state.a4, specialist_selection=next_selection),
        legacy_passthrough=frozen,
    )
    return Transition(
        next_state,
        (KernelEvent("specialist-recommendation-recorded"),),
    )


def _command_type_guard(expected: Type[object]) -> Callable[[MissionState, object], bool]:
    def guard(_state: MissionState, command: object) -> bool:
        return isinstance(command, expected)

    return guard


from .guidance import (
    _advance_guidance_guard,
    _rule_recipe,
    _stagnation_guidance_guard,
    guidance_transition_rules,
)


TRANSITION_TABLE = build_transition_table(
    (
        TransitionRule(
            "advance-phase",
            AdvancePhase,
            _command_type_guard(AdvancePhase),
            _advance,
            85,
            _advance_guidance_guard,
            _rule_recipe,
            (
                ("A4.plan-handoff", "PreparedPlanHandoff", "AdvancePhase"),
                ("A4.executor-handoff", "ExecutionObservation", "AdvancePhase"),
            ),
        ),
        TransitionRule(
            "artifact-initialize",
            InitializeArtifact,
            _command_type_guard(InitializeArtifact),
            _initialize_artifact,
        ),
        TransitionRule(
            "artifact-append-block",
            AppendArtifactBlock,
            _command_type_guard(AppendArtifactBlock),
            _append_artifact_block,
        ),
        TransitionRule(
            "artifact-render",
            RenderArtifact,
            _command_type_guard(RenderArtifact),
            _render_artifact,
        ),
        TransitionRule(
            "artifact-export",
            ExportArtifact,
            _command_type_guard(ExportArtifact),
            _export_artifact,
        ),
        TransitionRule(
            "artifact-record-publication",
            RecordArtifactPublication,
            _command_type_guard(RecordArtifactPublication),
            _record_artifact_publication,
        ),
        TransitionRule(
            "progress-update",
            UpdateProgress,
            _command_type_guard(UpdateProgress),
            _update_progress,
        ),
        TransitionRule(
            "progress-clear",
            ClearProgress,
            _command_type_guard(ClearProgress),
            _clear_progress,
        ),
        TransitionRule(
            "context-manifest-generate",
            GenerateContextManifest,
            _command_type_guard(GenerateContextManifest),
            _generate_context_manifest,
        ),
        TransitionRule(
            "verification-record",
            RecordVerification,
            _command_type_guard(RecordVerification),
            _record_verification,
        ),
        TransitionRule(
            "verification-receipt-record",
            RecordVerificationReceipt,
            _command_type_guard(RecordVerificationReceipt),
            _record_verification_receipt,
        ),
        TransitionRule(
            "fresh-review-prepare",
            PrepareFreshReview,
            _command_type_guard(PrepareFreshReview),
            _prepare_fresh_review,
        ),
        TransitionRule(
            "fresh-review-run", BeginFreshReviewDispatch, _command_type_guard(BeginFreshReviewDispatch), _fresh_review_dispatch,
        ),
        TransitionRule(
            "fresh-review-launch", RecordFreshReviewLaunch, _command_type_guard(RecordFreshReviewLaunch), _fresh_review_dispatch,
        ),
        TransitionRule(
            "fresh-review-withdraw", WithdrawFreshReviewRequest, _command_type_guard(WithdrawFreshReviewRequest), _fresh_review_dispatch,
        ),
        TransitionRule(
            "fresh-review-result", CommitFreshReviewResult, _command_type_guard(CommitFreshReviewResult), _fresh_review_dispatch,
        ),
        TransitionRule(
            "fresh-review-output-import", ImportFreshReviewOutput, _command_type_guard(ImportFreshReviewOutput), _fresh_review_dispatch,
        ),
        TransitionRule(
            "acceptance-contract-import",
            ImportAcceptanceContract,
            _command_type_guard(ImportAcceptanceContract),
            _import_acceptance_contract,
        ),
        TransitionRule(
            "claims-ledger-generate",
            GenerateClaimsLedger,
            _command_type_guard(GenerateClaimsLedger),
            _generate_claims_ledger,
        ),
        TransitionRule(
            "executor-handoff-begin",
            BeginExecutorHandoff,
            _command_type_guard(BeginExecutorHandoff),
            _begin_executor_handoff,
        ),
        TransitionRule(
            "executor-handoff-verify-step",
            VerifyExecutorStep,
            _command_type_guard(VerifyExecutorStep),
            _verify_executor_step,
        ),
        TransitionRule(
            "executor-handoff-record-step",
            RecordExecutorStep,
            _command_type_guard(RecordExecutorStep),
            _record_executor_step,
        ),
        TransitionRule(
            "executor-handoff-complete",
            CompleteExecutorHandoff,
            _command_type_guard(CompleteExecutorHandoff),
            _complete_executor_handoff,
        ),
        TransitionRule(
            "executor-handoff-reject-canonical-drift",
            RejectExecutorHandoff,
            _command_type_guard(RejectExecutorHandoff),
            _reject_executor_handoff,
        ),
        TransitionRule(
            "executor-handoff-abort",
            AbortExecutorHandoff,
            _command_type_guard(AbortExecutorHandoff),
            _abort_executor_handoff,
        ),
        TransitionRule(
            "specialists-record-recommendation",
            RecordSpecialistRecommendation,
            _command_type_guard(RecordSpecialistRecommendation),
            _record_specialist_recommendation,
        ),
        TransitionRule(
            "mark-halt",
            MarkHalt,
            _command_type_guard(MarkHalt),
            _mark_halt,
            60,
            _stagnation_guidance_guard,
            _rule_recipe,
            (("A1.mark-halt", "StagnationObservation", "MarkHalt"),),
        ),
        TransitionRule(
            "mark-pass",
            MarkPass,
            _command_type_guard(MarkPass),
            _mark_pass,
        ),
        TransitionRule(
            "budget-reserve-dispatch",
            ReserveDispatchBudget,
            _command_type_guard(ReserveDispatchBudget),
            _reserve_budget,
        ),
        TransitionRule(
            "budget-record-refusal",
            RecordBudgetRefusal,
            _command_type_guard(RecordBudgetRefusal),
            _record_budget_refusal,
        ),
        TransitionRule(
            "budget-settle-dispatch",
            SettleDispatchBudget,
            _command_type_guard(SettleDispatchBudget),
            _settle_budget,
        ),
        TransitionRule("budget-reconcile", ReconcileDispatchBudget,
                       _command_type_guard(ReconcileDispatchBudget), _reconcile_budget),
        TransitionRule(
            "budget-enter-final",
            EnterFinalPhase,
            _command_type_guard(EnterFinalPhase),
            _enter_final_budget,
        ),
        TransitionRule(
            "budget-stop",
            BudgetStopCommand,
            _command_type_guard(BudgetStopCommand),
            _budget_stop,
        ),
        TransitionRule(
            "reactivate",
            Reactivate,
            _command_type_guard(Reactivate),
            _reactivate,
        ),
        TransitionRule(
            "resume-stale",
            ResumeStale,
            _command_type_guard(ResumeStale),
            _resume_stale,
        ),
        TransitionRule(
            "specialist-selection-decline",
            DeclineSpecialistSelection,
            _command_type_guard(DeclineSpecialistSelection),
            _decline_specialist_selection,
        ),
        TransitionRule(
            "set-extension-fields",
            SetExtensionFields,
            _command_type_guard(SetExtensionFields),
            _set_extension_fields,
        ),
        *guidance_transition_rules(),
    )
)


def decide(state: MissionState, command: Command) -> Decision:
    if not isinstance(state, MissionState):
        return Decision(False, None, Rejection("invalid-state"))
    guarded = [
        rule
        for rule in TRANSITION_TABLE
        if isinstance(command, rule.command_type)
        and rule.command_guard(state, command)
    ]
    matches = [rule for rule in guarded if rule.reducer is not None]
    if len(matches) != 1:
        deferred = [rule for rule in guarded if rule.reducer is None]
        if len(deferred) == 1:
            return Decision(
                False,
                None,
                Rejection("external-command-authority-required"),
                deferred[0].rule_id,
            )
        return Decision(False, None, Rejection("unknown-command"))
    rule = matches[0]
    try:
        reducer = rule.reducer
        assert reducer is not None
        transition = reducer(state, command)
    except _Rejected as rejected:
        return Decision(False, None, Rejection(rejected.code), rule.rule_id)
    _register_transition(transition, state, command)
    return Decision(True, transition, None, rule.rule_id)


_TRANSITION_SEAL = object()
_ISSUED_TRANSITIONS: dict[
    int, tuple[weakref.ReferenceType[Transition], MissionState, Command]
] = {}


def _register_transition(
    transition: Transition,
    state: MissionState,
    command: Command,
) -> None:
    object.__setattr__(transition, "_seal", _TRANSITION_SEAL)
    object.__setattr__(transition, "_input_state", state)
    object.__setattr__(transition, "_command", command)
    identity = id(transition)

    def discard(_reference: object) -> None:
        _ISSUED_TRANSITIONS.pop(identity, None)

    _ISSUED_TRANSITIONS[identity] = (weakref.ref(transition, discard), state, command)


def is_sealed_transition(value: object) -> bool:
    """Return whether ``value`` was issued by the canonical decision table."""
    if not isinstance(value, Transition) or value._seal is not _TRANSITION_SEAL:
        return False
    registered = _ISSUED_TRANSITIONS.get(id(value))
    return registered is not None and registered[0]() is value


def is_transition_bound_to(
    transition: object,
    state: MissionState,
    command: Command,
) -> bool:
    """Verify the exact state and typed command that produced a transition."""
    if not is_sealed_transition(transition):
        return False
    registered = _ISSUED_TRANSITIONS[id(transition)]
    return registered[1] == state and registered[2] == command


def bind_transition_effects(
    transition: Transition,
    effects: tuple[object, ...],
) -> Transition:
    """Bind immutable verified effects without reopening the state decision."""
    if not is_sealed_transition(transition) or type(effects) is not tuple:
        raise TransitionTableError("invalid-transition-effect-binding")
    registered = _ISSUED_TRANSITIONS[id(transition)]
    command = registered[2]
    claims: tuple[object, ...] | None = None
    if isinstance(command, (PrepareFreshReview, ImportFreshReviewOutput)):
        claims = () if command.effect is None else (command.effect,)
    elif isinstance(command, (InitializeArtifact, RenderArtifact, RecordArtifactPublication)):
        claims = (command.effect,)
    elif isinstance(command, AppendArtifactBlock):
        claims = ()
    elif isinstance(command, ExportArtifact):
        claims = (command.artifact_effect, command.export_effect)
    elif isinstance(command, (UpdateProgress, GenerateContextManifest, GenerateClaimsLedger)):
        claims = (command.effect,)
    elif isinstance(command, (ClearProgress, RecordVerification, RecordVerificationReceipt, ImportAcceptanceContract,
                              BeginFreshReviewDispatch, RecordFreshReviewLaunch, CommitFreshReviewResult, WithdrawFreshReviewRequest)):
        claims = ()
    if claims is not None and (
        len(effects) != len(claims)
        or any(
            not all(
                getattr(effect, field_name, None) == getattr(claim, field_name)
                for field_name in ("kind", "target", "digest", "size")
            )
            for claim, effect in zip(claims, effects)
        )
    ):
        raise TransitionTableError("invalid-transition-effect-binding")
    bound = Transition(transition.new_state, transition.events, effects)
    _register_transition(bound, registered[1], registered[2])
    return bound
