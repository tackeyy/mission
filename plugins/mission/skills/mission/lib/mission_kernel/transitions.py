"""One deterministic table for the K2 state-only command subset."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
import re
import weakref
from typing import Any, Callable, Optional, Type

from mission_common import terminal_outcome_for_halt

from .commands import (
    ClearProgress,
    CompatibilityPayload,
    ContextManifestEffectClaim,
    DeclineSpecialistSelection,
    GENERIC_SET_DEDICATED_FIELDS,
    GENERIC_SET_FROZEN_FIELDS,
    AdvancePhase,
    AppendArtifactBlock,
    Command,
    ExportArtifact,
    GenerateContextManifest,
    InitializeArtifact,
    MarkHalt,
    MarkPass,
    Reactivate,
    RecordArtifactPublication,
    RecordVerification,
    RenderArtifact,
    ResumeStale,
    SetExtensionFields,
    ProgressEffectClaim,
    UpdateProgress,
)
from .evidence import (
    EvidenceRuleError,
    apply_context_manifest,
    apply_progress_clear,
    apply_progress_update,
    apply_verification_record,
)
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
    FrozenJsonObject,
    HaltCategory,
    MissionControl,
    MissionState,
    Phase,
    PreparedHandoff,
    SessionRole,
    TerminalOutcome,
)


class TransitionTableError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _Rejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _unbound_state(state: MissionState, **changes: Any) -> MissionState:
    return replace(state, snapshot_provenance=None, **changes)


@dataclass(frozen=True)
class KernelEvent:
    type: str


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
    return _unbound_state(state, **changes)


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
    return _unbound_state(state, **changes)


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
        if not isinstance(state.handoff, AbsentHandoff):
            raise _Rejected("handoff-already-exists")
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
        new_handoff = command.prepared_handoff
    elif command.prepared_handoff is not None:
        raise _Rejected("unexpected-prepared-handoff")
    new_control = replace(control, phase=command.target)
    new_state = _unbound_state(
            state,
            control=new_control,
            handoff=new_handoff,
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


def _mark_pass(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, MarkPass)
    control = _active_control(state)
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
    document["specialists_decision"] = {
        **checkpoint,
        "action": "continue-core",
        "decision": "declined",
        "reason": command.reason.strip(),
        "reason_code": "orchestrator-declined",
        "prompted_user": False,
        "lifecycle_state": "terminal",
    }
    document["specialists_mode"] = "manual"
    if command.at is not None:
        document["updated_at"] = command.at
    return Transition(
        _with_artifact_document(state, document),
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
    if isinstance(command, (InitializeArtifact, RenderArtifact, RecordArtifactPublication)):
        claims = (command.effect,)
    elif isinstance(command, AppendArtifactBlock):
        claims = ()
    elif isinstance(command, ExportArtifact):
        claims = (command.artifact_effect, command.export_effect)
    elif isinstance(command, (UpdateProgress, GenerateContextManifest)):
        claims = (command.effect,)
    elif isinstance(command, (ClearProgress, RecordVerification)):
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
