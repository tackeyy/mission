"""Closed guidance-only facts decoded from the authoritative state document."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
import math
from typing import Any, Mapping, Optional

from specialist_accounting import TERMINAL_SPECIALIST_INVOCATION_STATUSES

from .errors import MissionStateDecodeError
from .json_codec import freeze_json_value
from .model import (
    AbsentPlan,
    BoundScore,
    HaltCategory,
    FrozenJsonObject,
    LegacyScore,
    Phase,
    SnapshotProvenance,
    TerminalOutcome,
)


class Complexity(str, Enum):
    UNKNOWN = "Unknown"
    SIMPLE = "Simple"
    STANDARD = "Standard"
    COMPLEX = "Complex"
    CRITICAL = "Critical"


@dataclass(frozen=True)
class RoutingFacts:
    awaiting_user: bool
    complexity: Complexity
    force_mission: bool
    issue_ref: Optional[str]


@dataclass(frozen=True)
class PlanningGuidanceFacts:
    policy_version: Optional[int]
    provider_required: bool
    strategy: Optional[str]


@dataclass(frozen=True)
class ReviewGuidanceFacts:
    critic_has_new_scope: Optional[bool]
    tier: str
    tier_source: Optional[str]
    tier_signals: tuple[str, ...]


@dataclass(frozen=True)
class AdvisoryFacts:
    pregate: Optional["PregateProjection"] = None


@dataclass(frozen=True)
class PregateProjection:
    issue_ref: str
    subject_digest: str
    verdict: str
    gate_id: str
    evaluated_at: str


@dataclass(frozen=True)
class PrimaryProviderBinding:
    provider_id: str
    selection_id: str
    planning_contract_digest: str


@dataclass(frozen=True)
class ProviderSelection:
    skill: str
    provider_id: Optional[str]
    selection_id: Optional[str]
    planning_mode: Optional[str]
    planning_contract_digest: Optional[str]
    required: bool


@dataclass(frozen=True)
class ProviderInvocation:
    variant: str
    invocation_id: str
    phase: str
    iteration: int
    status: str
    lifecycle_state: str
    required: bool
    skill: str
    provider_id: Optional[str]
    selection_id: Optional[str]


@dataclass(frozen=True)
class ProviderGuidanceFacts:
    selections: tuple[ProviderSelection, ...] = ()
    invocations: tuple[ProviderInvocation, ...] = ()
    imported_invocation_ids: tuple[str, ...] = ()
    primary_binding: Optional[PrimaryProviderBinding] = None


@dataclass(frozen=True, init=False)
class GuidanceFacts:
    routing: RoutingFacts
    planning: PlanningGuidanceFacts
    review: ReviewGuidanceFacts
    advisories: AdvisoryFacts
    providers: ProviderGuidanceFacts
    provenance: SnapshotProvenance
    _snapshot_binding: Optional[object] = field(
        default=None, init=False, repr=False, compare=False
    )

    def __init__(self) -> None:
        raise TypeError("GuidanceFacts is issued by the paired reader")


def _new_guidance_facts(
    *,
    routing: RoutingFacts,
    planning: PlanningGuidanceFacts,
    review: ReviewGuidanceFacts,
    advisories: AdvisoryFacts,
    providers: ProviderGuidanceFacts,
    provenance: SnapshotProvenance,
) -> GuidanceFacts:
    value = object.__new__(GuidanceFacts)
    for name, field_value in (
        ("routing", routing),
        ("planning", planning),
        ("review", review),
        ("advisories", advisories),
        ("providers", providers),
        ("provenance", provenance),
        ("_snapshot_binding", None),
    ):
        object.__setattr__(value, name, field_value)
    return value


_GUIDANCE_KEYS = {"schema", "routing", "planning", "review", "advisories", "providers"}
_ROUTING_KEYS = {"awaiting_user", "complexity", "force_mission", "issue_ref"}
_PLANNING_KEYS = {"policy_version", "provider_required", "strategy"}
_REVIEW_KEYS = {"critic_has_new_scope", "tier", "tier_source", "tier_signals"}
_ADVISORY_KEYS = {"pregate"}
_PROVIDER_KEYS = {"primary_binding", "selections", "invocations", "imported_invocation_ids"}
MAX_GUIDANCE_COLLECTION_ITEMS = 128
MAX_GUIDANCE_SELECTIONS = 1024
MAX_GUIDANCE_INVOCATIONS = 10_000
MAX_GUIDANCE_PLAN_IMPORTS = MAX_GUIDANCE_INVOCATIONS
MAX_GUIDANCE_TOKEN_CHARS = 128
MAX_GUIDANCE_TEXT_CHARS = 2048
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INVOCATION_ID = re.compile(r"inv_[0-9a-f]{32}\Z")
_SELECTION_ID = re.compile(r"sel_[0-9a-f]{32}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
_PHASES = {"planning", "execution", "review", "scoring", "critic", "synthesis"}
_TERMINAL_STATUSES = {
    "rejected",
    "failed-before-start",
    "abandoned-unknown",
    "completed",
    "unvalidated-evidence",
    "prepared",
    "awaiting-input",
    "inline-applied",
    "skill-tool-applied",
    "skipped",
    "unavailable",
    "failed",
}
_INVOCATION_VARIANTS = {
    "selected": {"selected": "selected", "started": "invoked"},
    "reserved": {"reserved": "reserved"},
    "running": {"running": "running"},
    "terminal": {status: "terminal" for status in _TERMINAL_STATUSES},
}
_PREGATE_KEYS = {"issue_ref", "subject_digest", "verdict", "gate_id", "evaluated_at"}
_PRIMARY_BINDING_KEYS = {"provider_id", "selection_id", "planning_contract_digest"}
_SELECTION_KEYS = {
    "skill",
    "provider_id",
    "selection_id",
    "planning_mode",
    "planning_contract_digest",
    "required",
}
_INVOCATION_KEYS = {
    "variant",
    "invocation_id",
    "phase",
    "iteration",
    "status",
    "lifecycle_state",
    "required",
    "skill",
    "provider_id",
    "selection_id",
}


class GuidanceDerivationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NormalizedGuidance:
    next_action: Optional[str]
    details: Optional[FrozenJsonObject]
    command_sequence: Optional[tuple[str, ...]]


@dataclass(frozen=True)
class GuidanceStep:
    kind: str
    owner: str
    action: str
    required_observation: Optional[str] = None
    follow_up_command: Optional[str] = None


@dataclass(frozen=True)
class GuidanceRecipe:
    rule_id: str
    parity_status: str
    legacy_dependency_ids: tuple[str, ...]
    steps: tuple[GuidanceStep, ...]
    advisories: tuple[str, ...]
    normalized: Optional[NormalizedGuidance]
    continuation_commands: tuple[object, ...] = ()


@dataclass(frozen=True)
class ParityDependency:
    dependency_id: str
    boundary: str
    authority_inputs: tuple[str, ...]


PARITY_DEPENDENCY_INVENTORY = (
    ParityDependency(
        "application.clock-budget-override",
        "outside-parity",
        ("$.budget_minutes", "$.started_at", "iso_now()"),
    ),
    ParityDependency(
        "legacy.goal-dispatch",
        "legacy-required",
        (
            "$.goal_dispatch_requested",
            "$.goal_dispatch_source",
            "$.goal_dispatch_resolution_fallback_reason",
        ),
    ),
    ParityDependency(
        "legacy.host-observation",
        "legacy-required",
        ("detect_host()",),
    ),
)
_GOAL_ROUTING_DEPENDENCIES = tuple(
    dependency.dependency_id
    for dependency in PARITY_DEPENDENCY_INVENTORY
    if dependency.boundary == "legacy-required"
)


@dataclass(frozen=True)
class DeferredCommand:
    """Non-executable marker for a command still owned by a later adapter."""

    rule_id: str


def normalize_legacy_guidance(output: Mapping[str, Any]) -> NormalizedGuidance:
    next_action = output.get("next_action")
    if next_action is not None and not isinstance(next_action, str):
        raise GuidanceDerivationError("invalid-normalized-next-action")
    raw_details = output.get("details") if "details" in output else None
    details = None
    if raw_details is not None:
        frozen = freeze_json_value(raw_details)
        if not isinstance(frozen, FrozenJsonObject):
            raise GuidanceDerivationError("invalid-normalized-details")
        details = frozen
    raw_sequence = output.get("command_sequence") if "command_sequence" in output else None
    if raw_sequence is not None and (
        not isinstance(raw_sequence, list)
        or any(not isinstance(item, str) for item in raw_sequence)
    ):
        raise GuidanceDerivationError("invalid-normalized-command-sequence")
    command_sequence = None if raw_sequence is None else tuple(raw_sequence)
    return NormalizedGuidance(next_action, details, command_sequence)


_FOLLOW_UPS = {
    "A1.lifecycle": ("LifecycleObservation", "AdvancePhase"),
    "A1.refresh-resume": ("ProcessObservation", "Reactivate"),
    "A1.mark-halt": ("StagnationObservation", "MarkHalt"),
    "A2.critic-scope": ("CriticScopeObservation", "RecordCriticScope"),
    "A2.review": ("ReviewEvidence", "ImportReview"),
    "A2.review-score": ("ReviewAggregateEvidence", "RecordScore"),
    "A2.pass-authority": ("PassGateEvidence", "MarkPasses"),
    "A4.plan-handoff": ("PreparedPlanHandoff", "AdvancePhase"),
    "A4.executor-handoff": ("ExecutionObservation", "AdvancePhase"),
    "A4.planning-provider": ("ProviderLifecycleObservation", "RecordProviderLifecycle"),
    "A4.specialist-accounting": ("SpecialistClosureObservation", "RecordSpecialistClosure"),
    "A5.awaiting-user": ("UserResponseObservation", "RecordUserResponse"),
}


def _steps(
    next_action: str,
    dependencies: tuple[str, ...],
    contracts: tuple[tuple[str, str, str], ...],
) -> tuple[GuidanceStep, ...]:
    if not dependencies:
        kind = "report" if next_action.startswith("report-") else "local-command"
        return (GuidanceStep(kind, "K2", next_action),)
    available = {dependency: (observation, follow_up) for dependency, observation, follow_up in contracts}
    return tuple(
        GuidanceStep(
            "external-observation",
            dependency.split(".", 1)[0],
            next_action,
            available[dependency][0],
            available[dependency][1],
        )
        for dependency in dependencies
    )


def _advisories(guidance: GuidanceFacts) -> tuple[str, ...]:
    pregate = guidance.advisories.pregate
    if pregate is None or pregate.verdict == "accepted":
        return ()
    return (
        f"WARNING: pregate verdict={pregate.verdict}。planning 前に分割を解決してください",
    )


def _recipe(
    rule_id: str,
    next_action: str,
    *,
    details: Optional[dict[str, Any]] = None,
    command_sequence: Optional[list[str]] = None,
    dependencies: tuple[str, ...] = (),
    guidance: Optional[GuidanceFacts] = None,
    contracts: tuple[tuple[str, str, str], ...] = (),
    continuation_commands: tuple[object, ...] = (),
) -> GuidanceRecipe:
    output: dict[str, Any] = {"next_action": next_action}
    if details is not None:
        output["details"] = details
    if command_sequence is not None:
        output["command_sequence"] = command_sequence
    return GuidanceRecipe(
        rule_id=rule_id,
        parity_status="legacy-required" if dependencies else "exact",
        legacy_dependency_ids=dependencies,
        steps=_steps(next_action, dependencies, contracts),
        advisories=() if guidance is None else _advisories(guidance),
        normalized=normalize_legacy_guidance(output),
        continuation_commands=continuation_commands,
    )


def _happy_path_sequence(
    phase: str,
    reviewer_count: int,
    *,
    plan_mode: str = "subagent",
    adopt_core: bool = False,
) -> list[str]:
    plan_step = (
        "plan を artifact に記載 (inline #339)"
        if plan_mode == "inline"
        else "Skill: mission-planner"
    )
    steps = [
        plan_step,
        "mission-state.py advance --phase executing --activity active:implementation",
        "Skill: mission-executor",
        "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response",
        f"Skill: mission-reviewer x{reviewer_count} (1 message, parallel)",
        "mission-state.py review-import --iteration <i> --stdin (reviewer ごとに実行し review_evidence_ref.path を保持)",
        f"mission-state.py review-finalize --iteration <i> --input-ref <review_evidence_ref.path> (全 reviewer 分を反復) --min-reviewers {reviewer_count}",
        "mission-state.py closeout",
    ]
    if adopt_core and phase == "planning":
        steps.insert(1, "mission-state.py planning adopt-core --input <plan.json>")
    start = {"planning": 0, "executing": 2, "reviewing": 4}[phase]
    return steps[start:]


def _current_planning_invocations(state: object, guidance: GuidanceFacts) -> tuple[ProviderInvocation, ...]:
    iteration = state.control.iteration
    return tuple(
        item
        for item in guidance.providers.invocations
        if item.phase == "planning" and item.iteration == iteration
    )


def _planning_policy_action(state: object, guidance: GuidanceFacts) -> Optional[str]:
    if guidance.planning.policy_version != 1 or state.control.phase is not Phase.PLANNING:
        return None
    invocations = _current_planning_invocations(state, guidance)
    if any(item.status == "running" for item in invocations):
        return "reconcile-provider-invocation"
    if not isinstance(state.plan, AbsentPlan):
        return "run-executor"
    strategy = guidance.planning.strategy or "core"
    if strategy == "core":
        return "run-planner"
    if strategy == "provider-primary" and guidance.providers.primary_binding is None:
        return "run-planner"
    current = invocations[-1] if invocations else None
    if current is None:
        return "prepare-planning-provider"
    if current.status == "reserved":
        return "await-planning-approval"
    if current.status in {"selected", "started"}:
        return "invoke-planning-provider"
    if current.status == "completed":
        if current.invocation_id not in guidance.providers.imported_invocation_ids:
            return "import-planning-result"
        return (
            "promote-canonical-plan"
            if strategy == "provider-primary"
            else "run-planner-with-evidence"
        )
    if current.required or guidance.planning.provider_required:
        return "halt-required-planning-provider"
    return "run-planner"


def _score_payload(score: object) -> dict[str, Any]:
    if isinstance(score, (LegacyScore, BoundScore)):
        return score.payload.thaw()
    return {}


def _valid_composite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0 <= float(value) <= 5
    )


def _current_scores(state: object) -> tuple[object, ...]:
    scoring_iteration = state.control.iteration or 1
    return tuple(
        score
        for score in state.scores
        if _score_payload(score).get("iteration") == scoring_iteration
        and _valid_composite(_score_payload(score).get("composite"))
    )


def _unclosed_skills(guidance: GuidanceFacts) -> list[str]:
    selected = {item.skill for item in guidance.providers.selections}
    terminal = {
        item.skill
        for item in guidance.providers.invocations
        if item.status in TERMINAL_SPECIALIST_INVOCATION_STATUSES
    }
    return sorted(selected - terminal)


def _rule_recipe(state: object, guidance: GuidanceFacts, rule: object) -> GuidanceRecipe:
    rule_id = rule.rule_id

    def make(*args: Any, **kwargs: Any) -> GuidanceRecipe:
        kwargs["guidance"] = guidance
        kwargs["contracts"] = rule.continuation_contracts
        return _recipe(*args, **kwargs)

    control = state.control
    if rule_id == "terminal-evidence":
        return make(rule_id, "report-terminal")
    if rule_id == "terminal-halt":
        return make(rule_id, "report-blocker")
    if rule_id == "terminal-pass":
        return make(rule_id, "report-complete")
    if rule_id == "await-user":
        return make(rule_id, "await-user", dependencies=("A5.awaiting-user",))
    if rule_id == "resume-inactive":
        return make(rule_id, "resume", dependencies=("A1.refresh-resume",))
    if rule_id == "mark-halt":
        return make(rule_id, "consider-halt", dependencies=("A1.mark-halt",))
    if rule_id == "route-goal":
        return GuidanceRecipe(
            rule_id,
            "legacy-required",
            _GOAL_ROUTING_DEPENDENCIES,
            (
                GuidanceStep("legacy-command", "legacy", "legacy.goal-dispatch"),
                GuidanceStep("external-observation", "legacy", "legacy.host-observation"),
            ),
            _advisories(guidance),
            None,
        )
    if rule_id == "advance-phase" and state.control.phase is Phase.PLANNING:
        return make(
            rule_id,
            "run-executor",
            details={"planning_policy_version": 1},
            dependencies=("A4.plan-handoff",),
            guidance=guidance,
        )
    if rule_id == "planning-policy":
        action = _planning_policy_action(state, guidance)
        assert action is not None and action != "run-planner"
        dependency = (
            "A4.plan-handoff" if action == "run-executor" else "A4.planning-provider"
        )
        return make(
            f"planning-policy-{action}",
            action,
            details={"planning_policy_version": 1},
            dependencies=(dependency,),
            guidance=guidance,
        )
    effective_reviewers = control.reviewer_count
    if control.iteration >= 2 and guidance.review.critic_has_new_scope is False:
        effective_reviewers = min(effective_reviewers, 2)
    if rule_id == "planning-inline":
        adopt_core = (
            guidance.planning.policy_version == 1
            and guidance.planning.strategy in {None, "core"}
            and not guidance.planning.provider_required
        )
        return make(
            rule_id,
            "plan-inline",
            details={"plan_mode": "inline"},
            command_sequence=_happy_path_sequence(
                "planning",
                effective_reviewers,
                plan_mode="inline",
                adopt_core=adopt_core,
            ),
            dependencies=("A1.lifecycle", "A4.plan-handoff"),
            guidance=guidance,
        )
    if rule_id == "planning-external":
        adopt_core = (
            guidance.planning.policy_version == 1
            and guidance.planning.strategy in {None, "core"}
            and not guidance.planning.provider_required
        )
        return make(
            rule_id,
            "run-planner",
            command_sequence=_happy_path_sequence(
                "planning", effective_reviewers, adopt_core=adopt_core
            ),
            dependencies=("A4.plan-handoff",),
            guidance=guidance,
        )
    if rule_id == "advance-phase":
        from .commands import AdvancePhase

        return make(
            rule_id,
            "run-executor",
            command_sequence=_happy_path_sequence("executing", effective_reviewers),
            dependencies=("A4.executor-handoff",),
            guidance=guidance,
            continuation_commands=(AdvancePhase(Phase.REVIEWING),),
        )
    if rule_id == "review-scope":
        return make(
            rule_id,
            "record-critic-scope",
            details={"iteration": control.iteration},
            dependencies=("A2.critic-scope",),
            guidance=guidance,
        )
    if rule_id == "review-external":
        context_mode = (
            "bounded"
            if control.iteration >= 2 and guidance.review.critic_has_new_scope is False
            else "full"
        )
        return make(
            rule_id,
            "run-reviewers",
            details={
                "reviewer_count": effective_reviewers,
                "context_mode": context_mode,
                "parallel_spawn_required": True,
            },
            command_sequence=_happy_path_sequence("reviewing", effective_reviewers),
            dependencies=("A2.review",),
            guidance=guidance,
        )
    if rule_id == "score-evidence-retry":
        unclosed = _unclosed_skills(guidance)
        details = {"missing_findings_evidence": True}
        if unclosed:
            details["unclosed_specialists"] = unclosed
        return make(
            rule_id,
            "aggregate-reviews",
            details=details,
            dependencies=("A2.review-score", "A4.specialist-accounting"),
            guidance=guidance,
        )
    if rule_id == "mark-passes":
        unclosed = _unclosed_skills(guidance)
        return make(
            rule_id,
            "mark-passes",
            details={"unclosed_specialists": unclosed} if unclosed else {},
            dependencies=("A2.pass-authority",),
            guidance=guidance,
        )
    if rule_id == "aggregate-reviews":
        return make(
            rule_id,
            "aggregate-reviews",
            dependencies=("A2.review-score",),
            guidance=guidance,
        )
    raise AssertionError(f"unknown guidance rule {rule_id}")


def _is_simple_route(state: object, guidance: GuidanceFacts) -> bool:
    return (
        state.control.phase is Phase.PLANNING
        and state.control.iteration <= 1
        and guidance.routing.complexity is Complexity.SIMPLE
        and not guidance.review.tier_signals
        and guidance.review.tier_source != "user"
        and not guidance.routing.issue_ref
        and not guidance.routing.force_mission
        and state.control.session_role.value == "implementer"
        and not state.scores
    )


def _missing_findings_evidence(state: object) -> bool:
    scores = _current_scores(state)
    if not scores:
        return False
    latest = scores[-1]
    payload = _score_payload(latest)
    source = latest.source.value if isinstance(latest, BoundScore) else payload.get("score_source")
    if source != "scoring-json":
        return False
    if isinstance(latest, BoundScore):
        return latest.review_evidence_ref is None
    return not payload.get("findings_evidence_path")


def _advance_guidance_guard(state: object, guidance: GuidanceFacts) -> bool:
    return state.control.phase is Phase.EXECUTING or (
        state.control.phase is Phase.PLANNING
        and _planning_policy_action(state, guidance) == "run-executor"
    )


def _stagnation_guidance_guard(state: object, _guidance: GuidanceFacts) -> bool:
    return (
        state.control.stagnation_count >= 3
        and state.control.phase is not Phase.REVIEWING
    )


def _deferred_command_guard(rule_id: str):
    def guard(_state: object, command: object) -> bool:
        return isinstance(command, DeferredCommand) and command.rule_id == rule_id

    return guard


def guidance_transition_rules() -> tuple[object, ...]:
    from .transitions import TransitionRule

    contract_ids = {
        "await-user": ("A5.awaiting-user",),
        "resume-inactive": ("A1.refresh-resume",),
        "planning-policy": ("A4.plan-handoff", "A4.planning-provider"),
        "planning-inline": ("A1.lifecycle", "A4.plan-handoff"),
        "planning-external": ("A4.plan-handoff",),
        "review-scope": ("A2.critic-scope",),
        "review-external": ("A2.review",),
        "score-evidence-retry": ("A2.review-score", "A4.specialist-accounting"),
        "mark-passes": ("A2.pass-authority",),
        "aggregate-reviews": ("A2.review-score",),
    }

    definitions = (
        ("terminal-evidence", 10, lambda s, g: s.terminal_outcome is TerminalOutcome.COMPLETED_EVIDENCE),
        ("terminal-halt", 20, lambda s, g: bool(s.control.halt_reason)),
        ("terminal-pass", 30, lambda s, g: s.control.passes is True),
        ("await-user", 40, lambda s, g: g.routing.awaiting_user),
        ("resume-inactive", 50, lambda s, g: s.control.loop_active is False),
        ("route-goal", 70, _is_simple_route),
        ("planning-policy", 80, lambda s, g: (_planning_policy_action(s, g) not in {None, "run-planner", "run-executor"})),
        ("planning-inline", 90, lambda s, g: s.control.phase is Phase.PLANNING and g.routing.complexity is Complexity.STANDARD and s.control.iteration <= 1 and g.review.tier != "full"),
        ("planning-external", 100, lambda s, g: s.control.phase is Phase.PLANNING),
        ("review-scope", 120, lambda s, g: s.control.phase is Phase.REVIEWING and s.control.iteration >= 2 and g.review.critic_has_new_scope is None),
        ("review-external", 130, lambda s, g: s.control.phase is Phase.REVIEWING),
        ("score-evidence-retry", 140, lambda s, g: _missing_findings_evidence(s)),
        ("mark-passes", 150, lambda s, g: bool(_current_scores(s))),
        ("aggregate-reviews", 160, lambda s, g: True),
    )
    return tuple(
        TransitionRule(
            rule_id,
            DeferredCommand,
            _deferred_command_guard(rule_id),
            None,
            rank,
            guard,
            _rule_recipe,
            tuple(
                (dependency, _FOLLOW_UPS[dependency][0], _FOLLOW_UPS[dependency][1])
                for dependency in contract_ids.get(rule_id, ())
            ),
        )
        for rule_id, rank, guard in definitions
    )


def derive_next(state: object, guidance: GuidanceFacts) -> GuidanceRecipe:
    state_provenance = getattr(state, "snapshot_provenance", None)
    state_session = getattr(getattr(state, "identity", None), "session_id", None)
    if (
        not isinstance(guidance, GuidanceFacts)
        or state_provenance is None
        or state_provenance != guidance.provenance
        or state_session != state_provenance.session_id
        or getattr(state, "_snapshot_binding", None) is None
        or getattr(state, "_snapshot_binding", None)
        is not getattr(guidance, "_snapshot_binding", None)
    ):
        raise GuidanceDerivationError("snapshot-provenance-mismatch")
    from .transitions import TRANSITION_TABLE

    matches = [
        rule
        for rule in TRANSITION_TABLE
        if rule.guidance_guard is not None and rule.guidance_guard(state, guidance)
    ]
    if not matches:
        raise GuidanceDerivationError("missing-primary-guidance")
    best_rank = min(rule.guidance_rank for rule in matches)
    best = [rule for rule in matches if rule.guidance_rank == best_rank]
    if len(best) != 1:
        raise GuidanceDerivationError("equal-rank-primary-tie")
    rule = best[0]
    assert rule.guidance_factory is not None
    return rule.guidance_factory(state, guidance, rule)


def _fail(code: str, path: str, detail: str) -> MissionStateDecodeError:
    return MissionStateDecodeError(code, path, detail)


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("invalid-type", path, "expected an object")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], path: str) -> None:
    missing = keys - set(value)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required field is missing")
    extra = set(value) - keys
    if extra:
        raise _fail("unknown-key", path, f"unknown key(s): {sorted(extra)!r}")


def _exact_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise _fail("invalid-type", path, "expected a boolean")
    return value


def _optional_bool(value: Any, path: str) -> Optional[bool]:
    if value is None:
        return None
    return _exact_bool(value, path)


def _optional_text(value: Any, path: str) -> Optional[str]:
    if value is None:
        return None
    return _portable_text(value, path, MAX_GUIDANCE_TEXT_CHARS)


def _portable_text(value: Any, path: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _fail("invalid-value", path, "expected bounded trimmed non-empty text")
    return value


def _token(value: Any, path: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise _fail("invalid-value", path, "expected a bounded token")
    return value


def _optional_token(value: Any, path: str) -> Optional[str]:
    return None if value is None else _token(value, path)


def _digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise _fail("invalid-value", path, "expected a sha256 digest")
    return value


def _optional_digest(value: Any, path: str) -> Optional[str]:
    return None if value is None else _digest(value, path)


def _bounded_objects(
    value: Any, path: str, maximum: int = MAX_GUIDANCE_COLLECTION_ITEMS
) -> list[Any]:
    if not isinstance(value, list):
        raise _fail("invalid-type", path, "expected an array")
    if len(value) > maximum:
        raise _fail("collection-too-large", path, "too many guidance entries")
    return value


def _decode_pregate(value: Any, issue_ref: Optional[str]) -> Optional[PregateProjection]:
    if value is None:
        return None
    path = "$.guidance.advisories.pregate"
    raw = _object(value, path)
    _exact(raw, _PREGATE_KEYS, path)
    pregate_issue = _portable_text(
        raw["issue_ref"], f"{path}.issue_ref", MAX_GUIDANCE_TEXT_CHARS
    )
    if pregate_issue != issue_ref:
        raise _fail("invariant-violation", f"{path}.issue_ref", "pregate issue must match routing")
    verdict = raw["verdict"]
    if verdict not in {"accepted", "split-required", "rejected"}:
        raise _fail("unknown-variant", f"{path}.verdict", "unknown pregate verdict")
    evaluated_at = raw["evaluated_at"]
    if not isinstance(evaluated_at, str) or _TIMESTAMP.fullmatch(evaluated_at) is None:
        raise _fail("invalid-value", f"{path}.evaluated_at", "expected a canonical timestamp")
    try:
        datetime.strptime(evaluated_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _fail(
            "invalid-value", f"{path}.evaluated_at", "expected a real calendar timestamp"
        ) from exc
    return PregateProjection(
        issue_ref=pregate_issue,
        subject_digest=_digest(raw["subject_digest"], f"{path}.subject_digest"),
        verdict=verdict,
        gate_id=_token(raw["gate_id"], f"{path}.gate_id"),
        evaluated_at=evaluated_at,
    )


def _decode_primary_binding(value: Any) -> Optional[PrimaryProviderBinding]:
    if value is None:
        return None
    path = "$.guidance.providers.primary_binding"
    raw = _object(value, path)
    _exact(raw, _PRIMARY_BINDING_KEYS, path)
    selection_id = _token(raw["selection_id"], f"{path}.selection_id")
    if _SELECTION_ID.fullmatch(selection_id) is None:
        raise _fail("invalid-value", f"{path}.selection_id", "malformed selection identity")
    return PrimaryProviderBinding(
        provider_id=_token(raw["provider_id"], f"{path}.provider_id"),
        selection_id=selection_id,
        planning_contract_digest=_digest(
            raw["planning_contract_digest"], f"{path}.planning_contract_digest"
        ),
    )


def _decode_selections(value: Any) -> tuple[ProviderSelection, ...]:
    path = "$.guidance.providers.selections"
    result = []
    seen_selection_ids = set()
    for index, item in enumerate(
        _bounded_objects(value, path, MAX_GUIDANCE_SELECTIONS)
    ):
        item_path = f"{path}[{index}]"
        raw = _object(item, item_path)
        _exact(raw, _SELECTION_KEYS, item_path)
        mode = raw["planning_mode"]
        if mode not in {None, "primary", "advisory"}:
            raise _fail("unknown-variant", f"{item_path}.planning_mode", "unknown planning mode")
        selection_id = _optional_token(raw["selection_id"], f"{item_path}.selection_id")
        if selection_id is not None and _SELECTION_ID.fullmatch(selection_id) is None:
            raise _fail("invalid-value", f"{item_path}.selection_id", "malformed selection identity")
        if selection_id is not None and selection_id in seen_selection_ids:
            raise _fail("duplicate-value", path, "selection identities must be unique")
        if selection_id is not None:
            seen_selection_ids.add(selection_id)
        result.append(
            ProviderSelection(
                skill=_portable_text(
                    raw["skill"], f"{item_path}.skill", MAX_GUIDANCE_TOKEN_CHARS
                ),
                provider_id=_optional_token(raw["provider_id"], f"{item_path}.provider_id"),
                selection_id=selection_id,
                planning_mode=mode,
                planning_contract_digest=_optional_digest(
                    raw["planning_contract_digest"], f"{item_path}.planning_contract_digest"
                ),
                required=_exact_bool(raw["required"], f"{item_path}.required"),
            )
        )
    return tuple(result)


def _decode_invocations(value: Any) -> tuple[ProviderInvocation, ...]:
    path = "$.guidance.providers.invocations"
    result = []
    seen = set()
    for index, item in enumerate(
        _bounded_objects(value, path, MAX_GUIDANCE_INVOCATIONS)
    ):
        item_path = f"{path}[{index}]"
        raw = _object(item, item_path)
        _exact(raw, _INVOCATION_KEYS, item_path)
        variant = raw["variant"]
        if variant not in _INVOCATION_VARIANTS:
            raise _fail("unknown-variant", f"{item_path}.variant", "unknown invocation variant")
        status = raw["status"]
        lifecycle = raw["lifecycle_state"]
        if _INVOCATION_VARIANTS[variant].get(status) != lifecycle:
            raise _fail(
                "invariant-violation",
                f"{item_path}.lifecycle_state",
                "status and lifecycle variant disagree",
            )
        invocation_id = _token(raw["invocation_id"], f"{item_path}.invocation_id")
        if _INVOCATION_ID.fullmatch(invocation_id) is None:
            raise _fail("invalid-value", f"{item_path}.invocation_id", "malformed invocation identity")
        if invocation_id in seen:
            raise _fail("duplicate-value", path, "invocation identities must be unique")
        seen.add(invocation_id)
        phase = raw["phase"]
        if phase not in _PHASES:
            raise _fail("unknown-variant", f"{item_path}.phase", "unknown invocation phase")
        iteration = raw["iteration"]
        if type(iteration) is not int:
            raise _fail("invalid-type", f"{item_path}.iteration", "expected an integer")
        if not 0 <= iteration <= 1_000_000:
            raise _fail("invalid-value", f"{item_path}.iteration", "iteration is out of range")
        selection_id = _optional_token(raw["selection_id"], f"{item_path}.selection_id")
        if selection_id is not None and _SELECTION_ID.fullmatch(selection_id) is None:
            raise _fail("invalid-value", f"{item_path}.selection_id", "malformed selection identity")
        result.append(
            ProviderInvocation(
                variant=variant,
                invocation_id=invocation_id,
                phase=phase,
                iteration=iteration,
                status=status,
                lifecycle_state=lifecycle,
                required=_exact_bool(raw["required"], f"{item_path}.required"),
                skill=_portable_text(
                    raw["skill"], f"{item_path}.skill", MAX_GUIDANCE_TEXT_CHARS
                ),
                provider_id=_optional_token(raw["provider_id"], f"{item_path}.provider_id"),
                selection_id=selection_id,
            )
        )
    return tuple(result)


def _string_array(
    value: Any, path: str, maximum: int = MAX_GUIDANCE_COLLECTION_ITEMS
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail("invalid-type", path, "expected an array")
    if len(value) > maximum:
        raise _fail("collection-too-large", path, "too many guidance entries")
    result = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, str)
            or not item
            or len(item) > MAX_GUIDANCE_TOKEN_CHARS
        ):
            raise _fail("invalid-value", f"{path}[{index}]", "expected a bounded token")
        result.append(item)
    if len(set(result)) != len(result):
        raise _fail("duplicate-value", path, "guidance entries must be unique")
    return tuple(result)


def _invocation_id_array(value: Any, path: str) -> tuple[str, ...]:
    result = _string_array(value, path, MAX_GUIDANCE_PLAN_IMPORTS)
    for index, item in enumerate(result):
        if _INVOCATION_ID.fullmatch(item) is None:
            raise _fail(
                "invalid-value",
                f"{path}[{index}]",
                "malformed invocation identity",
            )
    return result


def _validate_imported_invocations(
    invocations: tuple[ProviderInvocation, ...],
    imported_ids: tuple[str, ...],
    path: str,
) -> None:
    by_id = {invocation.invocation_id: invocation for invocation in invocations}
    for index, invocation_id in enumerate(imported_ids):
        invocation = by_id.get(invocation_id)
        if (
            invocation is None
            or invocation.phase != "planning"
            or invocation.status != "completed"
            or invocation.lifecycle_state != "terminal"
        ):
            raise _fail(
                "invariant-violation",
                f"{path}[{index}]",
                "plan import must bind a completed planning invocation",
            )


def decode_v5_guidance(
    value: Any, provenance: SnapshotProvenance
) -> GuidanceFacts:
    raw = _object(value, "$.guidance")
    _exact(raw, _GUIDANCE_KEYS, "$.guidance")
    if raw["schema"] != "mission-guidance/1":
        raise _fail("unknown-variant", "$.guidance.schema", "unknown guidance schema")

    routing = _object(raw["routing"], "$.guidance.routing")
    _exact(routing, _ROUTING_KEYS, "$.guidance.routing")
    try:
        complexity = Complexity(routing["complexity"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            "unknown-variant", "$.guidance.routing.complexity", "unknown complexity"
        ) from exc

    planning = _object(raw["planning"], "$.guidance.planning")
    _exact(planning, _PLANNING_KEYS, "$.guidance.planning")
    policy_version = planning["policy_version"]
    if policy_version is not None and (type(policy_version) is not int or policy_version != 1):
        raise _fail(
            "unknown-variant",
            "$.guidance.planning.policy_version",
            "planning policy must be null or version 1",
        )
    strategy = planning["strategy"]
    if strategy not in {None, "core", "provider-primary", "provider-advisory"}:
        raise _fail(
            "unknown-variant", "$.guidance.planning.strategy", "unknown planning strategy"
        )

    review = _object(raw["review"], "$.guidance.review")
    _exact(review, _REVIEW_KEYS, "$.guidance.review")
    tier = review["tier"]
    if tier not in {"light", "standard", "full"}:
        raise _fail("unknown-variant", "$.guidance.review.tier", "unknown review tier")
    tier_source = review["tier_source"]
    if tier_source not in {None, "auto", "user"}:
        raise _fail(
            "unknown-variant", "$.guidance.review.tier_source", "unknown review tier source"
        )

    advisories = _object(raw["advisories"], "$.guidance.advisories")
    _exact(advisories, _ADVISORY_KEYS, "$.guidance.advisories")
    issue_ref = _optional_text(routing["issue_ref"], "$.guidance.routing.issue_ref")
    pregate = _decode_pregate(advisories["pregate"], issue_ref)

    providers = _object(raw["providers"], "$.guidance.providers")
    _exact(providers, _PROVIDER_KEYS, "$.guidance.providers")
    primary_binding = _decode_primary_binding(providers["primary_binding"])
    selections = _decode_selections(providers["selections"])
    invocations = _decode_invocations(providers["invocations"])
    imported = _invocation_id_array(
        providers["imported_invocation_ids"],
        "$.guidance.providers.imported_invocation_ids",
    )
    if tuple(sorted(imported)) != imported:
        raise _fail(
            "invalid-value",
            "$.guidance.providers.imported_invocation_ids",
            "invocation identities must be sorted",
        )
    _validate_imported_invocations(
        invocations,
        imported,
        "$.guidance.providers.imported_invocation_ids",
    )
    if primary_binding is not None:
        matches = [
            item
            for item in selections
            if item.planning_mode == "primary"
            and item.provider_id == primary_binding.provider_id
            and item.selection_id == primary_binding.selection_id
            and item.planning_contract_digest == primary_binding.planning_contract_digest
        ]
        if len(matches) != 1:
            raise _fail(
                "invariant-violation",
                "$.guidance.providers.primary_binding",
                "primary binding must match exactly one selection",
            )

    return _new_guidance_facts(
        routing=RoutingFacts(
            awaiting_user=_exact_bool(
                routing["awaiting_user"], "$.guidance.routing.awaiting_user"
            ),
            complexity=complexity,
            force_mission=_exact_bool(
                routing["force_mission"], "$.guidance.routing.force_mission"
            ),
            issue_ref=issue_ref,
        ),
        planning=PlanningGuidanceFacts(
            policy_version=policy_version,
            provider_required=_exact_bool(
                planning["provider_required"],
                "$.guidance.planning.provider_required",
            ),
            strategy=strategy,
        ),
        review=ReviewGuidanceFacts(
            critic_has_new_scope=_optional_bool(
                review["critic_has_new_scope"],
                "$.guidance.review.critic_has_new_scope",
            ),
            tier=tier,
            tier_source=tier_source,
            tier_signals=_string_array(
                review["tier_signals"], "$.guidance.review.tier_signals"
            ),
        ),
        advisories=AdvisoryFacts(pregate=pregate),
        providers=ProviderGuidanceFacts(
            selections=selections,
            invocations=invocations,
            imported_invocation_ids=imported,
            primary_binding=primary_binding,
        ),
        provenance=provenance,
    )


def guidance_payload(guidance: GuidanceFacts) -> dict[str, Any]:
    if not isinstance(guidance, GuidanceFacts):
        raise _fail("invalid-type", "$.guidance", "expected GuidanceFacts")
    pregate = guidance.advisories.pregate
    primary = guidance.providers.primary_binding
    return {
        "schema": "mission-guidance/1",
        "routing": {
            "awaiting_user": guidance.routing.awaiting_user,
            "complexity": guidance.routing.complexity.value,
            "force_mission": guidance.routing.force_mission,
            "issue_ref": guidance.routing.issue_ref,
        },
        "planning": {
            "policy_version": guidance.planning.policy_version,
            "provider_required": guidance.planning.provider_required,
            "strategy": guidance.planning.strategy,
        },
        "review": {
            "critic_has_new_scope": guidance.review.critic_has_new_scope,
            "tier": guidance.review.tier,
            "tier_source": guidance.review.tier_source,
            "tier_signals": list(guidance.review.tier_signals),
        },
        "advisories": {
            "pregate": None
            if pregate is None
            else {
                "issue_ref": pregate.issue_ref,
                "subject_digest": pregate.subject_digest,
                "verdict": pregate.verdict,
                "gate_id": pregate.gate_id,
                "evaluated_at": pregate.evaluated_at,
            }
        },
        "providers": {
            "primary_binding": None
            if primary is None
            else {
                "provider_id": primary.provider_id,
                "selection_id": primary.selection_id,
                "planning_contract_digest": primary.planning_contract_digest,
            },
            "selections": [
                {
                    "skill": item.skill,
                    "provider_id": item.provider_id,
                    "selection_id": item.selection_id,
                    "planning_mode": item.planning_mode,
                    "planning_contract_digest": item.planning_contract_digest,
                    "required": item.required,
                }
                for item in guidance.providers.selections
            ],
            "invocations": [
                {
                    "variant": item.variant,
                    "invocation_id": item.invocation_id,
                    "phase": item.phase,
                    "iteration": item.iteration,
                    "status": item.status,
                    "lifecycle_state": item.lifecycle_state,
                    "required": item.required,
                    "skill": item.skill,
                    "provider_id": item.provider_id,
                    "selection_id": item.selection_id,
                }
                for item in guidance.providers.invocations
            ],
            "imported_invocation_ids": list(
                guidance.providers.imported_invocation_ids
            ),
        },
    }


def _legacy_bool(document: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in document:
        return default
    value = document[key]
    if type(value) is not bool:
        raise _fail("invalid-type", f"$.{key}", "expected a boolean")
    return value


def _legacy_optional_bool(document: Mapping[str, Any], key: str) -> Optional[bool]:
    if key not in document or document[key] is None:
        return None
    value = document[key]
    if type(value) is not bool:
        raise _fail("invalid-type", f"$.{key}", "expected null or a boolean")
    return value


def _legacy_provider_facts(document: Mapping[str, Any]) -> ProviderGuidanceFacts:
    selections_raw = document.get("specialists_selected", [])
    selections = []
    seen_selection_ids = set()
    for index, item in enumerate(
        _bounded_objects(
            selections_raw, "$.specialists_selected", MAX_GUIDANCE_SELECTIONS
        )
    ):
        path = f"$.specialists_selected[{index}]"
        raw = _object(item, path)
        mode = raw.get("planning_mode")
        if mode not in {None, "primary", "advisory"}:
            raise _fail("unknown-variant", f"{path}.planning_mode", "unknown planning mode")
        selection_id = _optional_token(raw.get("selection_id"), f"{path}.selection_id")
        if selection_id is not None and _SELECTION_ID.fullmatch(selection_id) is None:
            raise _fail("invalid-value", f"{path}.selection_id", "malformed selection identity")
        if selection_id is not None and selection_id in seen_selection_ids:
            raise _fail(
                "duplicate-value",
                "$.specialists_selected",
                "selection identities must be unique",
            )
        if selection_id is not None:
            seen_selection_ids.add(selection_id)
        required = raw.get("required", False)
        selections.append(
            ProviderSelection(
                skill=_portable_text(
                    raw.get("skill"), f"{path}.skill", MAX_GUIDANCE_TOKEN_CHARS
                ),
                provider_id=_optional_token(raw.get("provider_id"), f"{path}.provider_id"),
                selection_id=selection_id,
                planning_mode=mode,
                planning_contract_digest=_optional_digest(
                    raw.get("planning_contract_digest"),
                    f"{path}.planning_contract_digest",
                ),
                required=_exact_bool(required, f"{path}.required"),
            )
        )

    invocations_raw = document.get("specialist_invocations", [])
    normalized_invocations = []
    for index, item in enumerate(
        _bounded_objects(
            invocations_raw,
            "$.specialist_invocations",
            MAX_GUIDANCE_INVOCATIONS,
        )
    ):
        path = f"$.specialist_invocations[{index}]"
        raw = _object(item, path)
        status = raw.get("status")
        lifecycle = raw.get("lifecycle_state")
        variant = next(
            (
                name
                for name, statuses in _INVOCATION_VARIANTS.items()
                if statuses.get(status) == lifecycle
            ),
            None,
        )
        if variant is None:
            raise _fail(
                "invariant-violation",
                f"{path}.lifecycle_state",
                "status and lifecycle variant disagree",
            )
        normalized_invocations.append(
            {
                "variant": variant,
                "invocation_id": raw.get("invocation_id"),
                "phase": raw.get("phase"),
                "iteration": raw.get("iteration"),
                "status": status,
                "lifecycle_state": lifecycle,
                "required": raw.get("required", False),
                "skill": raw.get("skill"),
                "provider_id": raw.get("provider_id"),
                "selection_id": raw.get("selection_id"),
            }
        )
    invocations = _decode_invocations(normalized_invocations)

    imports = document.get("provider_plan_imports", {})
    if not isinstance(imports, Mapping):
        raise _fail("invalid-type", "$.provider_plan_imports", "expected an object")
    if len(imports) > MAX_GUIDANCE_PLAN_IMPORTS:
        raise _fail("collection-too-large", "$.provider_plan_imports", "too many imports")
    imported_ids = []
    for invocation_id, record in imports.items():
        if not isinstance(invocation_id, str) or _INVOCATION_ID.fullmatch(invocation_id) is None:
            raise _fail("invalid-value", "$.provider_plan_imports", "malformed invocation identity")
        if not isinstance(record, Mapping) or record.get("invocation_id") != invocation_id:
            raise _fail(
                "invariant-violation",
                f"$.provider_plan_imports.{invocation_id}",
                "import must bind its invocation identity",
            )
        imported_ids.append(invocation_id)
    imported_ids.sort()
    _validate_imported_invocations(
        invocations,
        tuple(imported_ids),
        "$.provider_plan_imports",
    )

    binding_raw = document.get("planning_provider_binding")
    primary_binding = _decode_primary_binding(binding_raw)
    if primary_binding is not None:
        matches = [
            item
            for item in selections
            if item.planning_mode == "primary"
            and item.provider_id == primary_binding.provider_id
            and item.selection_id == primary_binding.selection_id
            and item.planning_contract_digest == primary_binding.planning_contract_digest
        ]
        if len(matches) != 1:
            raise _fail(
                "invariant-violation",
                "$.planning_provider_binding",
                "primary binding must match exactly one selection",
            )
    return ProviderGuidanceFacts(
        selections=tuple(selections),
        invocations=invocations,
        imported_invocation_ids=tuple(imported_ids),
        primary_binding=primary_binding,
    )


def decode_legacy_guidance(
    document: Mapping[str, Any], provenance: SnapshotProvenance
) -> GuidanceFacts:
    raw_complexity = document.get("complexity", Complexity.UNKNOWN.value)
    try:
        complexity = Complexity(raw_complexity)
    except (TypeError, ValueError) as exc:
        raise _fail("unknown-variant", "$.complexity", "unknown complexity") from exc
    policy_version = document.get("planning_policy_version")
    if policy_version is not None and (type(policy_version) is not int or policy_version != 1):
        raise _fail(
            "unknown-variant",
            "$.planning_policy_version",
            "planning policy must be absent or version 1",
        )
    strategy = document.get("planning_strategy")
    if strategy not in {None, "core", "provider-primary", "provider-advisory"}:
        raise _fail("unknown-variant", "$.planning_strategy", "unknown planning strategy")
    issue_ref = _optional_text(document.get("issue_ref"), "$.issue_ref")
    tier = document.get("review_tier", "standard")
    if tier not in {"light", "standard", "full"}:
        raise _fail("unknown-variant", "$.review_tier", "unknown review tier")
    tier_source = document.get("review_tier_source")
    if tier_source not in {None, "auto", "user"}:
        raise _fail("unknown-variant", "$.review_tier_source", "unknown review tier source")
    signals = _string_array(
        document.get("review_tier_signals", []), "$.review_tier_signals"
    )
    pregate_raw = document.get("pregate")
    pregate = None
    if pregate_raw is not None:
        raw = _object(pregate_raw, "$.pregate")
        pregate = _decode_pregate(
            {
                "issue_ref": issue_ref,
                "subject_digest": raw.get("subject_digest"),
                "verdict": raw.get("verdict"),
                "gate_id": raw.get("gate_id"),
                "evaluated_at": raw.get("evaluated_at"),
            },
            issue_ref,
        )
    return _new_guidance_facts(
        routing=RoutingFacts(
            awaiting_user=_legacy_bool(document, "awaiting_user", False),
            complexity=complexity,
            force_mission=_legacy_bool(document, "force_mission", False),
            issue_ref=issue_ref,
        ),
        planning=PlanningGuidanceFacts(
            policy_version=policy_version,
            provider_required=_legacy_bool(document, "planning_provider_required", False),
            strategy=strategy,
        ),
        review=ReviewGuidanceFacts(
            critic_has_new_scope=_legacy_optional_bool(document, "critic_has_new_scope"),
            tier=tier,
            tier_source=tier_source,
            tier_signals=signals,
        ),
        advisories=AdvisoryFacts(pregate=pregate),
        providers=_legacy_provider_facts(document),
        provenance=provenance,
    )
