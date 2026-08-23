"""One deterministic table for the K2 state-only command subset."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
import weakref
from typing import Any, Callable, Optional, Type

from .commands import (
    GENERIC_SET_DEDICATED_FIELDS,
    GENERIC_SET_FROZEN_FIELDS,
    AdvancePhase,
    Command,
    MarkHalt,
    MarkPass,
    Reactivate,
    ResumeStale,
    SetExtensionFields,
)
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
    return Transition(
        _unbound_state(
            state,
            control=new_control,
            handoff=new_handoff,
        ),
        (KernelEvent("phase-advanced"),),
    )


def _halt_outcome(category: HaltCategory, role: SessionRole) -> TerminalOutcome:
    if category is HaltCategory.EVIDENCE_SUBMITTED:
        return (
            TerminalOutcome.COMPLETED_EVIDENCE
            if role in {SessionRole.CHECKER, SessionRole.PLANNING, SessionRole.ANALYZE}
            else TerminalOutcome.INCOMPLETE
        )
    return {
        HaltCategory.PARTIAL_DONE: TerminalOutcome.INCOMPLETE,
        HaltCategory.BLOCKED_EXTERNAL: TerminalOutcome.BLOCKED_EXTERNAL,
        HaltCategory.AWAITING_APPROVAL: TerminalOutcome.AWAITING_APPROVAL,
        HaltCategory.STALE: TerminalOutcome.STALE_SUPERSEDED,
        HaltCategory.STAGNATION: TerminalOutcome.FAILED,
        HaltCategory.OTHER: TerminalOutcome.FAILED,
        HaltCategory.USER_ABORT: TerminalOutcome.USER_ABORTED,
        HaltCategory.ROUTED_GOAL: TerminalOutcome.ROUTED_ELSEWHERE,
    }[category]


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 2048:
        raise _Rejected("invalid-reason")
    return value


def _mark_halt(state: MissionState, raw_command: object) -> Transition:
    command = raw_command
    assert isinstance(command, MarkHalt)
    control = _active_control(state)
    if not isinstance(command.category, HaltCategory):
        raise _Rejected("unknown-halt-category")
    reason = _reason(command.reason)
    outcome = _halt_outcome(command.category, control.session_role)
    new_control = replace(
        control,
        phase=Phase.HALTED,
        terminal_outcome=outcome,
        loop_active=False,
        halt_reason=reason,
        halt_category=command.category,
    )
    return Transition(
        _unbound_state(state, control=new_control),
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
    new_control = replace(
        control,
        phase=target,
        terminal_outcome=None,
        loop_active=True,
        halt_reason="",
        halt_category=None,
    )
    return Transition(
        _unbound_state(state, control=new_control),
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
    new_control = replace(
        control,
        phase=target,
        terminal_outcome=None,
        loop_active=True,
        halt_reason="",
        halt_category=None,
    )
    return Transition(
        _unbound_state(state, control=new_control),
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
    return Transition(
        _unbound_state(state, control=new_control),
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
    fields = command.fields
    if not isinstance(fields, FrozenJsonObject) or not fields.items:
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
    if state.legacy_passthrough is not None:
        document = dict(state.legacy_passthrough.items)
        document.update(fields.items)
        changes["legacy_passthrough"] = FrozenJsonObject(tuple(document.items()))
    return Transition(
        _unbound_state(state, **changes),
        (KernelEvent("extension-fields-set"),),
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


# Completion-adjacent control fields whose transition claims the persistence
# layer can verify and apply today.  Two exclusions remain deliberate:
# halt_reason — the kernel receives the stripped semantic reason while the
# compatibility contract persists the raw legacy value; terminal_outcome —
# ``derive_terminal_outcome`` carries legacy reason-precedence rules (a
# supersede-form reason forces stale_superseded regardless of category) that
# can legitimately diverge from the kernel's category mapping.  Unifying the
# two derivations is 批2-a-3 scope.
_CLAIMABLE_CONTROL_FIELDS = ("phase", "loop_active", "passes", "halt_category")


def transition_control_claim_bounds(
    transition: object,
) -> dict[str, tuple[object, object]]:
    """Return ``{field: (before, after)}`` for the claimed control changes.

    A claim is a control field whose value differs between the decision's
    input state and ``new_state``.  The ``before`` value lets the persistence
    layer distinguish an untouched compatibility document (apply the claim)
    from a writer that produced a third value (re-implementation drift).
    """
    if not is_sealed_transition(transition):
        raise TransitionTableError("invalid-transition-claim")
    assert isinstance(transition, Transition)
    registered = _ISSUED_TRANSITIONS[id(transition)]
    before = registered[1].control
    after = transition.new_state.control
    claims: dict[str, tuple[object, object]] = {}
    for field_name in _CLAIMABLE_CONTROL_FIELDS:
        if getattr(before, field_name) != getattr(after, field_name):
            claims[field_name] = (
                getattr(before, field_name),
                getattr(after, field_name),
            )
    return claims


def transition_control_claims(transition: object) -> dict[str, object]:
    """Return the sealed transition's claimed completion-adjacent changes."""
    return {
        field_name: after
        for field_name, (_before, after) in transition_control_claim_bounds(
            transition
        ).items()
    }


def bind_transition_effects(
    transition: Transition,
    effects: tuple[object, ...],
) -> Transition:
    """Bind immutable verified effects without reopening the state decision."""
    if not is_sealed_transition(transition) or type(effects) is not tuple:
        raise TransitionTableError("invalid-transition-effect-binding")
    registered = _ISSUED_TRANSITIONS[id(transition)]
    bound = Transition(transition.new_state, transition.events, effects)
    _register_transition(bound, registered[1], registered[2])
    return bound
