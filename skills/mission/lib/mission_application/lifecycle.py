"""Lifecycle application use cases shared by the CLI adapters."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from typing import Callable

from activity_segments import (
    ActivityTimingError,
    PHASE_ACTIVITY_DEFAULTS,
    close_activity_for_resume,
    close_activity_for_terminal,
    end_activity_segment,
    record_activity_event,
    start_activity_segment,
    start_phase_default_activity,
    validate_activity,
)
from artifact_contract import invalidate_artifact_lint_observation
from mission_kernel.codec_v4 import decode_mission_state
from mission_common import derive_terminal_outcome, is_supersede_marked
from mission_kernel.commands import (
    GENERIC_SET_DEDICATED_FIELDS,
    GENERIC_SET_FROZEN_FIELDS,
    AdvancePhase,
    MarkHalt,
    Reactivate as ReactivateCommand,
    ResumeStale,
    SetExtensionFields,
)
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.model import HaltCategory, Phase, PreparedHandoff
from mission_kernel.transitions import Decision, decide, transition_control_claim_bounds
from .ports import AggregateIndexError, LegacyMissionRepository, MissionInitializer


LIFECYCLE_COMMAND_OWNERS = {
    "activity-end": "A1.lifecycle",
    "activity-start": "A1.lifecycle",
    "advance": "A1.lifecycle",
    "cleanup-stale": "A1.lifecycle",
    "halt": "A1.lifecycle",
    "init": "A1.lifecycle",
    "mark-halt": "A1.lifecycle",
    "reactivate": "A1.lifecycle",
    "refresh-pid": "A1.lifecycle",
    "resume": "A1.lifecycle",
    "set": "A1.lifecycle",
    "update-project-root": "A1.lifecycle",
}


# The closed field classification for generic ``set`` is kernel authority
# (#617 批1-a); this module re-exports it for the CLI adapters.
DEDICATED_SET_FIELDS = GENERIC_SET_DEDICATED_FIELDS


def monotonic_halt_decision(raw_state: dict, category: str, reason: str) -> Decision:
    """Decide a monotonic (synthetic-view) halt for janitor-style terminalization.

    Adapters that terminalize other sessions' possibly degraded legacy
    documents (supersede-reviews) use this gate: the synthetic view keeps the
    emergency halt decidable on malformed v1-v4 documents, while the claims
    verification in ``repository.execute`` still pins the written phase /
    loop_active to the kernel's decision (批2-a-1 #630).
    """
    try:
        halt_category = HaltCategory(category)
    except ValueError as error:
        raise LifecycleFailure(
            "unknown halt category: %r" % (category,),
            reason="unknown-halt-category",
            outcome_kind="invalid-input",
        ) from error
    return decide(
        _mark_halt_decision_state(raw_state),
        MarkHalt(
            halt_category,
            reason,
            superseded=is_supersede_marked(raw_state.get("resolution_status"), reason),
        ),
    )


def extension_fields_decision(raw_state: dict, fields: dict) -> Decision:
    """Decide a generic extension write under the kernel's closed field authority.

    The synthetic monotonic view is reused so administrative adapters can gate
    property writes on documents that predate typed validation; the kernel's
    frozen / dedicated field classification still applies unchanged.
    """
    try:
        frozen_fields = freeze_json_value(fields)
    except ValueError as error:
        raise LifecycleFailure(
            "set fields payload is invalid: %s" % error,
            reason="invalid-set-fields",
            outcome_kind="invalid-input",
        ) from error
    return decide(
        _mark_halt_decision_state(raw_state), SetExtensionFields(frozen_fields)
    )


@dataclass(frozen=True)
class InitRequest:
    arguments: object


def initialize(repository: MissionInitializer, request: InitRequest) -> None:
    """Create or resume a session through the v4 repository boundary.

    New-session v5 selection remains P1 scope, so A1 deliberately preserves
    the complete v4 initializer behind this application boundary.
    """
    repository.initialize(request.arguments)


@dataclass(frozen=True)
class ActivityStartRequest:
    kind: str
    reason: str
    at: str
    detail: str | None
    resume: bool


@dataclass(frozen=True)
class ActivityEndRequest:
    at: str


@dataclass(frozen=True)
class ActivityResult:
    changed: bool
    activity_current: dict | None


class LifecycleFailure(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        outcome_kind: str = "expected-gate",
        guided: bool = False,
        state: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.outcome_kind = outcome_kind
        self.guided = guided
        self.state = state


@dataclass(frozen=True)
class AdvanceRequest:
    phase: str
    activity: str | None
    at: str
    detail: str | None
    artifact_applicability: str | None
    artifact_path: str | None
    producer_run_id: str | None


@dataclass(frozen=True)
class AdvanceServices:
    reject_active_provider_mutation: Callable[[dict, str], None]
    prepare_handoff: Callable[[dict], dict | None]
    capture_artifact: Callable[[str, str], tuple[dict, object]]
    transition_phase: Callable[[dict, str, str], None]


@dataclass(frozen=True)
class AdvanceResult:
    phase: str | None
    activity_current: dict | None
    decision: Decision | None


@dataclass(frozen=True)
class MarkHaltRequest:
    reason: str
    category: str
    at: str
    set_terminal_phase: bool = True


@dataclass(frozen=True)
class MarkHaltServices:
    reject_active_provider_mutation: Callable[[dict, str], None]
    transition_phase: Callable[..., None]
    goal_dispatch_fields: Callable[[dict], dict]
    terminalize_without_phase: Callable[[dict, str, bool], None] | None = None
    effective_at: Callable[[dict, str], str] | None = None


@dataclass(frozen=True)
class MarkHaltResult:
    halt_reason: str
    halt_category: str
    decision: Decision
    aggregate_error: str | None


@dataclass(frozen=True)
class ReactivateRequest:
    approved_by_user: bool
    reason: str
    expected_category: str
    phase: str
    at: str


@dataclass(frozen=True)
class ReactivateResult:
    audit: dict
    decision: Decision
    aggregate_error: str | None


@dataclass(frozen=True)
class RefreshPidRequest:
    new_pid: int
    force: bool
    reactivate: bool
    at: str


@dataclass(frozen=True)
class RefreshPidServices:
    lease_fields_present: Callable[[dict], bool]
    pid_is_agent: Callable[[int], bool]
    resume_phase_timing: Callable[[dict, str], None]


@dataclass(frozen=True)
class RefreshPidResult:
    old_pid: object
    new_pid: int
    reactivated: bool
    previous_halt_reason: str
    previous_loop_active: object
    decision: Decision | None
    aggregate_error: str | None


@dataclass(frozen=True)
class UpdateProjectRootRequest:
    new_root: str
    at: str


@dataclass(frozen=True)
class UpdateProjectRootResult:
    old_root: str
    new_root: str


@dataclass(frozen=True)
class SetFieldsRequest:
    kvs: tuple[str, ...]
    at: str


@dataclass(frozen=True)
class SetFieldsServices:
    frozen_fields: frozenset[str]
    reject_active_provider_mutation: Callable[[dict, str], None]
    normalize_phase: Callable[[str], str]
    transition_phase: Callable[[dict, str, str], None]
    ensure_phase_timing: Callable[[dict, str], None]
    derive_review_tier: Callable[[str, object, object], tuple[str, object]]
    derive_review_tier_decision: Callable[[str, object, object], dict]
    reviewer_count_by_tier: dict[str, int]
    goal_dispatch_fields: Callable[[dict], dict]
    goal_dispatch_guidance: Callable[[dict, str], object]


@dataclass(frozen=True)
class SetFieldsResult:
    routed_verdict: dict | None
    decision: Decision | None
    warnings: tuple[str, ...]
    aggregate_error: str | None


@dataclass(frozen=True)
class _GoalRoutePlan:
    decision: Decision
    verdict: dict


@dataclass(frozen=True)
class _SetFieldsPlan:
    document: dict
    warnings: tuple[str, ...]
    route: _GoalRoutePlan | None


def activity_start(repository: LegacyMissionRepository, request: ActivityStartRequest) -> ActivityResult:
    with repository.transaction():
        state = repository.load()

        def mutate(proposed: dict) -> None:
            changed_holder[0] = start_activity_segment(
                proposed,
                request.kind,
                request.reason,
                request.at,
                detail=request.detail,
                resume=request.resume,
                origin="manual",
            )
            if changed_holder[0]:
                proposed["updated_at"] = request.at

        changed_holder = [False]
        proposed = repository.execute(state, mutate)
        if changed_holder[0]:
            repository.save(proposed)
    return ActivityResult(changed_holder[0], proposed.get("activity_current"))


def activity_end(repository: LegacyMissionRepository, request: ActivityEndRequest) -> ActivityResult:
    with repository.transaction():
        state = repository.load()

        def mutate(proposed: dict) -> None:
            changed_holder[0] = end_activity_segment(proposed, request.at)
            if changed_holder[0]:
                proposed["updated_at"] = request.at

        changed_holder = [False]
        proposed = repository.execute(state, mutate)
        if changed_holder[0]:
            repository.save(proposed)
    return ActivityResult(changed_holder[0], proposed.get("activity_current"))


def _typed_state(raw_state: dict):
    compatible = copy.deepcopy(raw_state)
    plan = compatible.get("canonical_plan")
    if isinstance(plan, dict):
        # K1's canonical reader requires validation lineage that pre-K1 v4
        # producers did not persist.  Fill only absent shadow-view fields;
        # present malformed authority still fails closed, and raw v4 bytes are
        # never rewritten from this compatibility view.
        plan.setdefault("schema", "mission-plan/1")
        plan.setdefault("source_digest", plan.get("digest"))
        plan.setdefault(
            "validated_at",
            compatible.get("updated_at") or compatible.get("started_at"),
        )
    return decode_mission_state(
        json.dumps(compatible, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _mark_halt_decision_state(raw_state: dict):
    """Build a minimal safe decision view for the monotonic halt command.

    Historical v1-v4 documents may contain malformed unrelated fields written
    before typed validation existed.  Emergency halt must not decode those
    fields, but it also must not project them back into authoritative state.
    The repository still persists the original document through the legacy
    reducer; this view exists only for the closed kernel decision.
    """
    phase = raw_state.get("phase")
    active_phases = {item.value for item in Phase} - {"done", "halted"}
    if not isinstance(phase, str) or phase not in active_phases:
        phase = "planning"
    return _typed_state(
        {
            "schema_version": 4,
            "phase": phase,
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "session_role": "implementer",
        }
    )


def real_terminalizable_state(document: dict):
    """Return a decoded active state suitable as the source of halt claims."""
    try:
        candidate = _typed_state(document)
    except (TypeError, ValueError, UnicodeError):
        return None
    if (
        candidate.control.phase not in {Phase.DONE, Phase.HALTED}
        and candidate.control.passes is False
        and not candidate.control.halt_reason
        and candidate.terminal_outcome is None
    ):
        return candidate
    return None


def _advance_decision(
    state: dict,
    request: AdvanceRequest,
    prepared_handoff: dict | None,
) -> Decision:
    typed = _typed_state(state)
    prepared = None
    if prepared_handoff is not None:
        candidate = copy.deepcopy(state)
        compatible_handoff = copy.deepcopy(prepared_handoff)
        plan = candidate.get("canonical_plan")
        if isinstance(plan, dict) and "iteration" in plan:
            # Legacy writers bind handoff.iteration to the session counter,
            # while K2 binds it to the selected plan.  Normalize only the
            # decision shadow; persisted v4 bytes retain the legacy value.
            compatible_handoff["iteration"] = plan["iteration"]
        candidate["executor_handoff"] = compatible_handoff
        decoded_candidate = _typed_state(candidate)
        if not isinstance(decoded_candidate.handoff, PreparedHandoff):
            raise LifecycleFailure(
                "prepared executor handoff is invalid",
                reason="invalid-prepared-handoff",
            )
        # Legacy v4 persists ``mission-executor-handoff/1`` while the K2
        # canonical command intentionally names the general handoff union.
        prepared = replace(decoded_candidate.handoff, schema="mission-handoff/1")
    return decide(typed, AdvancePhase(Phase(request.phase), prepared))


def advance(
    repository: LegacyMissionRepository,
    request: AdvanceRequest,
    services: AdvanceServices,
) -> AdvanceResult:
    if request.phase in {"done", "halted"}:
        with repository.transaction():
            state = repository.load()
        raise LifecycleFailure(
            "advance で terminal phase へは遷移できません。"
            " 合格は mark-passes、中断は mark-halt を使ってください。",
            reason="terminal-phase",
            guided=True,
            state=state,
        )
    if request.activity is None:
        default = PHASE_ACTIVITY_DEFAULTS.get(request.phase)
        if default is None:
            raise LifecycleFailure(
                "phase '%s' has no default activity." % request.phase,
                reason="activity-default",
                outcome_kind="invalid-input",
            )
        kind, reason = default
        origin = "phase-default"
    else:
        kind, separator, reason = request.activity.partition(":")
        if not separator or not kind or not reason:
            raise LifecycleFailure(
                "--activity は <kind>:<reason> 形式で指定してください "
                "(例: active:implementation)。受領値: '%s'" % request.activity,
                reason="activity-format",
                outcome_kind="invalid-input",
                guided=True,
            )
        origin = None
    validate_activity(kind, reason)

    with repository.transaction():
        state = repository.load()
        services.reject_active_provider_mutation(state, "advance")
        prepared_handoff = None
        is_phase_change = state.get("phase") != request.phase
        if (
            request.phase == "executing"
            and is_phase_change
            and state.get("planning_policy_version") == 1
        ):
            if not isinstance(state.get("canonical_plan"), dict):
                raise LifecycleFailure(
                    "policy v1 requires a canonical plan before executing",
                    reason="missing-canonical-plan",
                    guided=True,
                    state=state,
                )
            if state.get("executor_handoff") is not None:
                raise LifecycleFailure(
                    "executor handoff already exists; use handoff resume",
                    reason="handoff-already-exists",
                    state=state,
                )
            prepared_handoff = services.prepare_handoff(state)
        decision = None
        candidate = None
        deferred_error = None
        if is_phase_change:
            try:
                candidate = _advance_decision(state, request, prepared_handoff)
            except Exception as error:  # noqa: BLE001 - preserve legacy mutation ordering
                deferred_error = error

        def mutate(proposed: dict) -> None:
            if prepared_handoff is not None:
                proposed["executor_handoff"] = prepared_handoff
            requested = request.artifact_applicability
            if requested == "producing":
                if not request.artifact_path or not request.producer_run_id:
                    raise LifecycleFailure(
                        "producing artifact handoff requires --artifact-path and --producer-run-id",
                        reason="producing-artifact",
                        outcome_kind="invalid-input",
                        guided=True,
                        state=proposed,
                    )
                identity, _content = services.capture_artifact(
                    request.artifact_path, request.producer_run_id
                )
                proposed["artifact"] = identity
                proposed["artifact_applicability"] = "producing"
                invalidate_artifact_lint_observation(proposed)
            elif requested == "not-applicable":
                if request.artifact_path or request.producer_run_id:
                    raise LifecycleFailure(
                        "not-applicable artifact handoff cannot include artifact identity",
                        reason="artifact-identity-unexpected",
                        outcome_kind="invalid-input",
                    )
                if proposed.get("artifact_applicability") == "producing":
                    raise LifecycleFailure(
                        "cannot downgrade producing artifact applicability to not-applicable",
                        reason="artifact-applicability-downgrade",
                    )
                proposed["artifact_applicability"] = "not-applicable"
            elif request.artifact_path or request.producer_run_id:
                raise LifecycleFailure(
                    "artifact identity requires --artifact-applicability producing",
                    reason="artifact-applicability-required",
                    outcome_kind="invalid-input",
                )
            if (
                proposed.get("phase") == "executing"
                and request.phase == "reviewing"
                and proposed.get("artifact_applicability") == "pending"
            ):
                raise LifecycleFailure(
                    "artifact applicability is pending; resolve it to producing or not-applicable before review",
                    reason="artifact-applicability-pending",
                )
            if (
                isinstance(proposed.get("activity_current"), dict)
                and proposed.get("phase") != request.phase
            ):
                end_activity_segment(proposed, request.at)
            services.transition_phase(proposed, request.phase, request.at)
            start_activity_segment(
                proposed,
                kind,
                reason,
                request.at,
                detail=request.detail,
                origin=origin,
            )
            proposed["updated_at"] = request.at

        proposed = repository.execute(
            state,
            mutate,
            candidate.transition if (candidate is not None and candidate.accepted) else None,
        )
        if is_phase_change:
            if deferred_error is not None:
                raise deferred_error
            # The v4 reducer owns compatibility gates and their exact error
            # ordering.  K2 is authoritative for transitions represented by
            # its closed subset; legacy skip-ahead transitions remain on the
            # compatibility path until their production switch is planned.
            if candidate is not None and candidate.accepted:
                decision = candidate
            elif (
                state.get("phase") == "executing"
                and request.phase == "reviewing"
            ) or (
                request.phase == "executing"
                and state.get("phase") != "executing"
                and state.get("planning_policy_version") == 1
            ):
                if candidate is None:
                    raise RuntimeError("advance decision was neither available nor deferred")
                if candidate.rejection is None:
                    raise RuntimeError("rejected advance decision has no rejection")
                raise LifecycleFailure(
                    "phase transition rejected: " + candidate.rejection.code,
                    reason=candidate.rejection.code,
                    state=state,
                )
        repository.save(proposed)
    return AdvanceResult(
        proposed.get("phase"), proposed.get("activity_current"), decision
    )


def mark_halt(
    repository: LegacyMissionRepository,
    request: MarkHaltRequest,
    services: MarkHaltServices,
) -> MarkHaltResult:
    with repository.transaction():
        state = repository.load()
        services.reject_active_provider_mutation(state, "mark-halt")
        effective_at = (
            services.effective_at(state, request.at)
            if services.effective_at is not None
            else request.at
        )
        # v4 accepted surrounding whitespace and repeated terminalization.
        # K2 receives the semantic reason, while the raw legacy value remains
        # the persisted compatibility contract.
        semantic_reason = request.reason.strip() or "legacy-empty-reason"
        command = MarkHalt(
            HaltCategory(request.category),
            semantic_reason,
            superseded=is_supersede_marked(state.get("resolution_status"), request.reason),
        )
        # 批2-a-2 (#631): decode 可能かつ active な state は実 state で decide
        # する（claims の halt_category / role 依存 outcome が実 state に基づく）。
        # terminal / 劣化 doc は monotonic view へ fallback し（冪等 emergency
        # halt の保証）、その場合は gate-only（transition 非送付）。
        real_state = real_terminalizable_state(state)
        decision = decide(
            real_state if real_state is not None else _mark_halt_decision_state(state),
            command,
        )
        if not decision.accepted:
            assert decision.rejection is not None
            raise LifecycleFailure(
                "halt rejected: " + decision.rejection.code,
                reason=decision.rejection.code,
                state=state,
            )
        # Empty legacy reasons are normalized only for the kernel gate; their
        # raw persisted value remains empty and therefore retains the historic
        # incomplete outcome.  They cannot carry a terminal-outcome claim.
        transition = decision.transition if (
            real_state is not None and request.set_terminal_phase and request.reason.strip()
        ) else None
        claimed = set(transition_control_claim_bounds(transition)) if transition is not None else set()

        def mutate(proposed: dict) -> None:
            if request.category == "awaiting-approval":
                record_activity_event(proposed, "awaiting-approval", effective_at)
            proposed["halt_reason"] = request.reason
            if "halt_category" not in claimed:
                proposed["halt_category"] = request.category
            if "loop_active" not in claimed:
                proposed["loop_active"] = False
            if request.category == "routed-goal":
                dispatch = services.goal_dispatch_fields(proposed)
                proposed["goal_dispatch_effective"] = dispatch["goal_dispatch_effective"]
                proposed["goal_dispatch_host"] = dispatch["goal_dispatch_host"]
                if dispatch.get("goal_dispatch_fallback_reason"):
                    proposed["goal_dispatch_fallback_reason"] = dispatch[
                        "goal_dispatch_fallback_reason"
                    ]
                else:
                    proposed.pop("goal_dispatch_fallback_reason", None)
            if request.set_terminal_phase:
                services.transition_phase(
                    proposed,
                    "halted",
                    effective_at,
                    terminal_trusted_boundary=request.category == "stale",
                )
            elif services.terminalize_without_phase is not None:
                services.terminalize_without_phase(
                    proposed,
                    effective_at,
                    request.category == "stale",
                )
            else:
                raise LifecycleFailure(
                    "terminal phase compatibility reducer is unavailable",
                    reason="terminal-reducer-missing",
                )
            if "terminal_outcome" not in claimed:
                proposed.pop("terminal_outcome", None)
                outcome = derive_terminal_outcome(proposed)
                if outcome is None:
                    raise LifecycleFailure(
                        "terminal transition did not produce a terminal outcome",
                        reason="terminal-outcome-missing",
                    )
                proposed["terminal_outcome"] = outcome
            proposed["updated_at"] = effective_at

        # set_terminal_phase=False（janitor の orphan 経路）は kernel の主張
        # (phase→halted) から意図的に逸脱する soft-terminal のため kernel 対象外
        # （gate-only）と確定する（批2-a-2 #631）。monotonic fallback（terminal /
        # 劣化 doc）も synthetic 入力由来の claims を適用しないため gate-only。
        proposed = repository.execute(
            state,
            mutate,
            transition,
        )
        aggregate_error = None
        try:
            repository.save(proposed, aggregate_action="remove")
        except AggregateIndexError as error:
            aggregate_error = str(error)
    return MarkHaltResult(
        request.reason,
        request.category,
        decision,
        aggregate_error,
    )


def reactivate(
    repository: LegacyMissionRepository,
    request: ReactivateRequest,
) -> ReactivateResult:
    if request.approved_by_user is not True:
        raise LifecycleFailure(
            "reactivate には --approved-by-user が必須です。",
            reason="approval-required",
        )
    if not request.reason:
        raise LifecycleFailure(
            "reactivate の --reason は空にできません。",
            reason="invalid-reason",
            outcome_kind="invalid-input",
        )
    with repository.transaction():
        state = repository.load()
        previous_reason = state.get("halt_reason") or ""
        raw_category = state.get("halt_category")
        previous_category = raw_category if raw_category not in (None, "") else "unknown"
        previous_phase = state.get("phase") or "unknown"
        if state.get("passes") is True:
            raise LifecycleFailure(
                "合格済み mission は reactivate できません。",
                reason="terminal-state",
                state=state,
            )
        if state.get("loop_active") is not False or not previous_reason:
            raise LifecycleFailure(
                "reactivate 対象の停止中 mission ではありません。",
                reason="manual-halt-required",
                state=state,
            )
        legacy_stale = (
            raw_category in (None, "", "unknown")
            and isinstance(previous_reason, str)
            and previous_reason.startswith(("orphan:", "stale:"))
        )
        if raw_category == "stale" or legacy_stale:
            raise LifecycleFailure(
                "stale/orphan halt は reactivate ではなく resume を使用してください。",
                reason="stale-requires-resume",
                state=state,
            )
        normalized_category = (
            raw_category
            if isinstance(raw_category, str)
            and raw_category in {item.value for item in HaltCategory}
            else "unknown"
        )
        if request.expected_category != normalized_category:
            raise LifecycleFailure(
                "--expected-category が現在の halt_category と一致しません: "
                "expected=%r actual=%r normalized=%r"
                % (request.expected_category, previous_category, normalized_category),
                reason="halt-category-mismatch",
                state=state,
            )
        decision_state = state
        decision_category = normalized_category
        if normalized_category == "unknown":
            # Pre-K2 states may contain an absent or malformed category.  The
            # audited v4 compatibility path confirms it as ``unknown`` while
            # the closed kernel sees the conservative generic halt variant.
            decision_state = copy.deepcopy(state)
            decision_state["halt_category"] = HaltCategory.OTHER.value
            decision_state["terminal_outcome"] = "failed"
            decision_category = HaltCategory.OTHER.value
        try:
            expected_category = HaltCategory(decision_category)
            target = Phase(request.phase)
        except ValueError as error:
            raise LifecycleFailure(
                "reactivate command contains an unknown variant",
                reason="unknown-variant",
                outcome_kind="invalid-input",
                state=state,
            ) from error
        decision = decide(
            _typed_state(decision_state),
            ReactivateCommand(
                expected_category,
                request.reason,
                request.approved_by_user,
                target,
            ),
        )
        if not decision.accepted:
            assert decision.rejection is not None
            raise LifecycleFailure(
                "reactivate rejected: " + decision.rejection.code,
                reason=decision.rejection.code,
                state=state,
            )
        history = state.get("reactivation_history")
        if history is not None and not isinstance(history, list):
            raise LifecycleFailure(
                "reactivation_history が不正なため再活性化できません。",
                reason="reactivation-history-invalid",
                state=state,
            )
        audit = {
            "timestamp": request.at,
            "previous_halt_reason": previous_reason,
            "previous_halt_category": previous_category,
            "previous_phase": previous_phase,
            "approved_reason": request.reason,
            "approved_by_user": True,
            "target_phase": request.phase,
        }

        def mutate(proposed: dict) -> None:
            close_activity_for_terminal(proposed, request.at, trusted_boundary=True)
            proposed["halt_reason"] = ""
            proposed.pop("halt_category", None)
            proposed.pop("terminal_outcome", None)
            proposed.pop("resume_target_phase", None)
            proposed["loop_active"] = True
            proposed["phase"] = request.phase
            proposed["phase_started_at"] = request.at
            start_activity_segment(
                proposed,
                "active",
                "resumed-implementation",
                request.at,
                detail=request.reason,
                resume=True,
            )
            proposed.setdefault("reactivation_history", []).append(audit)
            proposed["updated_at"] = request.at

        proposed = repository.execute(state, mutate, decision.transition)
        aggregate_error = None
        try:
            repository.save(proposed, aggregate_action="add")
        except AggregateIndexError as error:
            aggregate_error = str(error)
    return ReactivateResult(audit, decision, aggregate_error)


def refresh_pid(
    repository: LegacyMissionRepository,
    request: RefreshPidRequest,
    services: RefreshPidServices,
) -> RefreshPidResult:
    with repository.transaction():
        state = repository.load()
        current = state.get("activity_current")
        should_close_activity = not (
            isinstance(current, dict) and current.get("started_at") == request.at
        )
        old_pid = state.get("pid")
        if (
            not services.lease_fields_present(state)
            and old_pid
            and isinstance(old_pid, int)
            and old_pid != request.new_pid
            and services.pid_is_agent(old_pid)
            and not request.force
        ):
            raise LifecycleFailure(
                "既存の owner pid=%s が agent CLI プロセスとして alive です。"
                " 別セッションが現役の可能性があるため拒否しました。"
                " 強制継承するには --force を指定してください。" % old_pid,
                reason="live-owner",
                state=state,
            )
        previous_reason = state.get("halt_reason", "")
        previous_category = state.get("halt_category")
        previous_loop = state.get("loop_active", False)
        legacy_stale = (
            previous_category in (None, "", "unknown")
            and isinstance(previous_reason, str)
            and previous_reason.startswith(("orphan:", "stale:"))
        )
        was_reactivatable = previous_category == "stale" or legacy_stale
        resume_target = state.get("resume_target_phase")
        valid_targets = {"planning", "executing", "reviewing", "scoring"}
        phase_can_reactivate = (
            state.get("phase") != "halted" or resume_target in valid_targets
        )
        reactivated = (
            was_reactivatable and request.reactivate and phase_can_reactivate
        )
        restored_phase = reactivated and state.get("phase") == "halted"
        decision = None
        if reactivated:
            target_phase = resume_target if restored_phase else state.get("phase")
            if target_phase not in valid_targets:
                raise LifecycleFailure(
                    "stale resume target is invalid",
                    reason="invalid-reactivation-target",
                    state=state,
                )
            decision_state = copy.deepcopy(state)
            decision_state["phase"] = "halted"
            decision_state["loop_active"] = False
            decision_state["halt_category"] = "stale"
            decision_state["terminal_outcome"] = "stale_superseded"
            decision = decide(
                _typed_state(decision_state),
                ResumeStale(Phase(target_phase)),
            )
            if not decision.accepted:
                assert decision.rejection is not None
                raise LifecycleFailure(
                    "stale resume rejected: " + decision.rejection.code,
                    reason=decision.rejection.code,
                    state=state,
                )

        def mutate(proposed: dict) -> None:
            proposed.clear()
            proposed.update(copy.deepcopy(state))
            if should_close_activity:
                close_activity_for_resume(proposed, request.at)
            proposed["pid"] = request.new_pid
            if reactivated:
                if restored_phase:
                    proposed["phase"] = resume_target
                    proposed["phase_started_at"] = request.at
                    proposed.pop("resume_target_phase", None)
                proposed["halt_reason"] = ""
                proposed.pop("halt_category", None)
                proposed.pop("terminal_outcome", None)
                proposed["loop_active"] = True
            if not restored_phase:
                services.resume_phase_timing(proposed, request.at)
            if proposed.get("loop_active") is not False and not proposed.get(
                "activity_current"
            ):
                start_phase_default_activity(proposed, request.at)
            proposed["updated_at"] = request.at

        proposed = repository.execute(state, mutate, decision.transition if decision else None)
        aggregate_error = None
        try:
            repository.save(
                proposed,
                aggregate_action="add" if reactivated else None,
            )
        except AggregateIndexError as error:
            aggregate_error = str(error)
    return RefreshPidResult(
        old_pid,
        request.new_pid,
        reactivated,
        previous_reason,
        previous_loop,
        decision,
        aggregate_error,
    )


def update_project_root(
    repository: LegacyMissionRepository,
    request: UpdateProjectRootRequest,
) -> UpdateProjectRootResult:
    with repository.transaction():
        state = repository.load()
        old_root = state.get("project_root", "")

        def mutate(proposed: dict) -> None:
            proposed["project_root"] = request.new_root
            proposed["updated_at"] = request.at

        proposed = repository.execute(state, mutate)
        repository.save(proposed)
    return UpdateProjectRootResult(old_root, request.new_root)


def set_fields(
    repository: LegacyMissionRepository,
    request: SetFieldsRequest,
    services: SetFieldsServices,
) -> SetFieldsResult:
    explicit_keys = {value.partition("=")[0] for value in request.kvs}
    warnings = []
    with repository.transaction():
        state = repository.load()
        services.reject_active_provider_mutation(state, "set")
        if "reviewer_count" in explicit_keys and not (
            {"complexity", "review_tier"} & explicit_keys
        ):
            raise LifecycleFailure(
                "`reviewer_count` は単独 set 不可。変更する場合は `complexity` "
                "または `review_tier` と同時に指定してください "
                "(A-2: agreement gate 無効化の防止)。",
                reason="reviewer-count",
                guided=True,
                state=state,
            )
        for field, reason, message in (
            (
                "halt_category",
                "halt-category",
                "`halt_category` は set で変更不可。変更は mark-halt / "
                "refresh-pid / resume 経由でのみ行ってください "
                "(A-3: 無承認 reactivate の防止)。",
            ),
            (
                "halt_reason",
                "halt-reason",
                "`halt_reason` は set で変更不可。明示 halt の解除は "
                "`reactivate --approved-by-user` を使用してください "
                "(A-3: 承認監査を伴わない再活性化の防止)。",
            ),
        ):
            if field in explicit_keys:
                raise LifecycleFailure(
                    message,
                    reason=reason,
                    guided=True,
                    state=state,
                )
        if "loop_active" in explicit_keys and state.get("halt_reason"):
            raw_loop = next(
                (
                    value
                    for key, separator, value in (
                        item.partition("=") for item in request.kvs
                    )
                    if separator and key == "loop_active"
                ),
                None,
            )
            try:
                requested_loop = json.loads(raw_loop) if raw_loop is not None else None
            except json.JSONDecodeError:
                requested_loop = raw_loop
            if requested_loop is True:
                raise LifecycleFailure(
                    "halt中の `loop_active=true` は set で変更不可。"
                    " `reactivate --approved-by-user` を使用してください。",
                    reason="loop-active-halt",
                    state=state,
                )
        remaining_dedicated = explicit_keys & DEDICATED_SET_FIELDS
        if remaining_dedicated:
            field = sorted(remaining_dedicated)[0]
            raise LifecycleFailure(
                "`%s` は set で変更不可。専用commandを使用してください。" % field,
                reason="dedicated-field",
                guided=True,
                state=state,
            )

        # The adapter-level checks above own the guided v4 error messages; the
        # closed kernel decision below is the fail-closed authority for the
        # same field classification (#617 批1-a).
        parsed_fields: dict[str, object] = {}
        for item in request.kvs:
            if "=" not in item:
                raise LifecycleFailure(
                    "key=value 形式で指定してください: " + item,
                    reason="key-value-format",
                    outcome_kind="invalid-input",
                )
            key, _separator, value = item.partition("=")
            if key in services.frozen_fields:
                raise LifecycleFailure(
                    "`%s` は set で変更不可。新しい mission は `init` を使用してください "
                    "(mission_id が再計算されます)。" % key,
                    reason="frozen-field",
                )
            if key == "review_tier":
                if value not in services.reviewer_count_by_tier:
                    raise LifecycleFailure(
                        "review_tier の値 '%s' は無効です。有効値: %s"
                        % (value, list(services.reviewer_count_by_tier)),
                        reason="review-tier-invalid",
                        outcome_kind="invalid-input",
                    )
                parsed_fields[key] = value
                continue
            try:
                parsed_fields[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed_fields[key] = value
        try:
            command_fields = freeze_json_value(parsed_fields)
        except ValueError as error:
            raise LifecycleFailure(
                "set fields payload is invalid: %s" % error,
                reason="invalid-set-fields",
                outcome_kind="invalid-input",
            ) from error
        try:
            typed_state = _typed_state(state)
        except (TypeError, ValueError, UnicodeError) as error:
            raise LifecycleFailure(
                "set requires a decodable session state: %s" % error,
                reason="state-undecodable",
                state=state,
            ) from error
        set_decision = decide(typed_state, SetExtensionFields(command_fields))
        if not set_decision.accepted:
            assert set_decision.rejection is not None
            code = set_decision.rejection.code
            if code == "frozen-field":
                field = sorted(explicit_keys & GENERIC_SET_FROZEN_FIELDS)[0]
                raise LifecycleFailure(
                    "`%s` は set で変更不可。新しい mission は `init` を使用してください "
                    "(mission_id が再計算されます)。" % field,
                    reason="frozen-field",
                )
            if code == "dedicated-field":
                field = sorted(explicit_keys & GENERIC_SET_DEDICATED_FIELDS)[0]
                raise LifecycleFailure(
                    "`%s` は set で変更不可。専用commandを使用してください。" % field,
                    reason="dedicated-field",
                    guided=True,
                    state=state,
                )
            raise LifecycleFailure(
                "set fields payload is invalid: " + code,
                reason=code,
                outcome_kind="invalid-input",
                state=state,
            )

        routed_verdict_holder = [None]
        decision_holder = [None]

        def route_simple_to_goal(proposed: dict) -> None:
            # This deliberately narrow conjunction is the existing #330 route
            # authority.  Do not simplify it: each exclusion prevents routing a
            # state with real mission, risk, review, or user-selection authority.
            if not (
                "complexity" in explicit_keys
                and proposed.get("complexity") == "Simple"
                and proposed.get("loop_active") is True
                and not proposed.get("halt_reason")
                and (proposed.get("phase") or "planning") == "planning"
                and (proposed.get("iteration") or 1) <= 1
                and not proposed.get("review_tier_signals")
                and proposed.get("review_tier_source") != "user"
                and not proposed.get("issue_ref")
                and not proposed.get("force_mission")
                and (proposed.get("session_role") or "implementer")
                == "implementer"
                and not (proposed.get("score_history") or [])
            ):
                return
            decision = decide(
                _typed_state(proposed),
                MarkHalt(
                    HaltCategory.ROUTED_GOAL,
                    "routed-to-goal (#330: Simple + リスクシグナルなし)",
                    superseded=is_supersede_marked(
                        proposed.get("resolution_status"),
                        "routed-to-goal (#330: Simple + リスクシグナルなし)",
                    ),
                ),
            )
            if not decision.accepted:
                assert decision.rejection is not None
                raise LifecycleFailure(
                    "goal route rejected: " + decision.rejection.code,
                    reason=decision.rejection.code,
                    state=proposed,
                )
            dispatch = services.goal_dispatch_fields(proposed)
            proposed["goal_dispatch_effective"] = dispatch[
                "goal_dispatch_effective"
            ]
            proposed["goal_dispatch_host"] = dispatch["goal_dispatch_host"]
            if dispatch.get("goal_dispatch_fallback_reason"):
                proposed["goal_dispatch_fallback_reason"] = dispatch[
                    "goal_dispatch_fallback_reason"
                ]
            else:
                proposed.pop("goal_dispatch_fallback_reason", None)
            proposed["loop_active"] = False
            proposed["halt_reason"] = (
                "routed-to-goal (#330: Simple + リスクシグナルなし)"
            )
            proposed["halt_category"] = "routed-goal"
            services.transition_phase(proposed, "halted", request.at)
            proposed.pop("terminal_outcome", None)
            outcome = derive_terminal_outcome(proposed)
            if outcome is None:
                raise LifecycleFailure(
                    "terminal transition did not produce a terminal outcome",
                    reason="terminal-outcome-missing",
                )
            proposed["terminal_outcome"] = outcome
            decision_holder[0] = decision
            routed_verdict_holder[0] = {
                "ok": True,
                "route": "goal",
                "complexity": "Simple",
                "reason": "Simple complexity with no irreversible/security signals (#330)",
                "guidance": services.goal_dispatch_guidance(
                    dispatch,
                    "state は routed-goal で halt 済み (mark-halt 不要)。mission ループを続けず、",
                ),
                **dispatch,
            }

        def mutate(proposed: dict) -> None:
            for item in request.kvs:
                if "=" not in item:
                    raise LifecycleFailure(
                        "key=value 形式で指定してください: " + item,
                        reason="key-value-format",
                        outcome_kind="invalid-input",
                    )
                key, _separator, value = item.partition("=")
                if key == "review_tier":
                    if value not in services.reviewer_count_by_tier:
                        raise LifecycleFailure(
                            "review_tier の値 '%s' は無効です。有効値: %s"
                            % (value, list(services.reviewer_count_by_tier)),
                            reason="review-tier-invalid",
                            outcome_kind="invalid-input",
                        )
                    mission = proposed.get("mission", "")
                    complexity = proposed.get("complexity")
                    risk = (proposed.get("task_profile") or {}).get("risk")
                    derived_tier, _details = services.derive_review_tier(
                        mission, complexity, risk
                    )
                    tier_order = {"light": 0, "standard": 1, "full": 2}
                    if tier_order.get(value, 0) < tier_order.get(derived_tier, 0):
                        warnings.append(
                            "WARNING [#168]: review_tier='%s' は auto 導出値 '%s' より低いです。"
                            " ゲート意味論 (threshold/open_high/findings evidence/halt) は変わりません。"
                            % (value, derived_tier)
                        )
                    proposed["review_tier"] = value
                    proposed["review_tier_source"] = "user"
                    continue
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = value
                proposed[key] = parsed

            if "complexity" in explicit_keys:
                tier_source = proposed.get("review_tier_source", "auto")
                if tier_source == "user":
                    if (
                        "reviewer_count" not in explicit_keys
                        and proposed.get("review_tier")
                        in services.reviewer_count_by_tier
                    ):
                        proposed["reviewer_count"] = services.reviewer_count_by_tier[
                            proposed["review_tier"]
                        ]
                else:
                    decision_value = services.derive_review_tier_decision(
                        proposed.get("mission", ""),
                        proposed.get("complexity"),
                        (proposed.get("task_profile") or {}).get("risk"),
                    )
                    tier = decision_value["tier"]
                    proposed["review_tier"] = tier
                    proposed["review_tier_source"] = "auto"
                    proposed["review_tier_signals"] = decision_value["signals"]
                    proposed["review_tier_signal_details"] = decision_value[
                        "signal_details"
                    ]
                    if "reviewer_count" not in explicit_keys:
                        proposed["reviewer_count"] = services.reviewer_count_by_tier[
                            tier
                        ]
            elif (
                "review_tier" in explicit_keys
                and "reviewer_count" not in explicit_keys
                and proposed.get("review_tier") in services.reviewer_count_by_tier
            ):
                proposed["reviewer_count"] = services.reviewer_count_by_tier[
                    proposed["review_tier"]
                ]

            route_simple_to_goal(proposed)
            services.ensure_phase_timing(proposed, request.at)
            proposed["updated_at"] = request.at

        # Build the compatibility projection exactly once.  The reducer calls
        # injected services while forming this shadow, never from execute's
        # mutation callback; duplicate key ordering therefore remains intact.
        shadow = copy.deepcopy(state)
        mutate(shadow)
        route = (
            _GoalRoutePlan(decision_holder[0], routed_verdict_holder[0])
            if decision_holder[0] is not None
            else None
        )
        plan = _SetFieldsPlan(shadow, tuple(warnings), route)

        def apply_plan(proposed: dict) -> None:
            proposed.clear()
            proposed.update(plan.document)

        transition = plan.route.decision.transition if plan.route is not None else set_decision.transition
        proposed = repository.execute(state, apply_plan, transition)
        routed_verdict = plan.route.verdict if plan.route is not None else None
        decision = plan.route.decision if plan.route is not None else set_decision
        aggregate_action = (
            "remove"
            if routed_verdict is not None
            else (
                "add"
                if "loop_active" in explicit_keys
                and proposed.get("loop_active") is True
                else None
            )
        )
        aggregate_error = None
        try:
            repository.save(
                proposed,
                administrative=routed_verdict is not None,
                aggregate_action=aggregate_action,
            )
        except AggregateIndexError as error:
            aggregate_error = str(error)
    return SetFieldsResult(
        routed_verdict,
        decision,
        plan.warnings,
        aggregate_error,
    )
