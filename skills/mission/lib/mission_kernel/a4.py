"""Closed A4 projections owned by handoff and specialist reducers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional

from provider_public_contract import (
    SpecialistPublicContractError,
    validate_specialist_public_state,
)
from specialist_lifecycle import (
    SpecialistLifecycleError,
    validate_specialist_lifecycle,
)

from .json_codec import freeze_json_value
from .model import FrozenJsonObject


_DECISION_FIELDS = frozenset(
    {
        "handoff_id",
        "plan_digest",
        "plan_generation",
        "plan_source",
        "source_id",
        "selection_source",
        "iteration",
        "step_id",
        "result",
    }
)


class A4ProjectionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExecutorStepDecision:
    handoff_id: str
    plan_digest: str
    plan_generation: int
    plan_source: str
    source_id: str
    selection_source: str
    iteration: int
    step_id: str
    result: str


@dataclass(frozen=True)
class SpecialistSelectionProjection:
    task_profile: Optional[FrozenJsonObject] = None
    candidates: tuple[FrozenJsonObject, ...] = ()
    selected: tuple[FrozenJsonObject, ...] = ()
    unavailable: tuple[FrozenJsonObject, ...] = ()
    ineligible: tuple[FrozenJsonObject, ...] = ()
    registry_projection: Optional[FrozenJsonObject] = None
    decision: Optional[FrozenJsonObject] = None
    phase_plan: tuple[FrozenJsonObject, ...] = ()
    mode: Optional[str] = None
    active_provider_invocation_ids: tuple[str, ...] = ()
    planning_policy_version: Optional[int] = None
    planning_strategy: Optional[str] = None
    planning_contract_digest: Optional[str] = None
    planning_provider_binding: Optional[FrozenJsonObject] = None


@dataclass(frozen=True)
class SpecialistRecommendationProjection:
    task_profile: FrozenJsonObject
    candidates: tuple[FrozenJsonObject, ...]
    selected: tuple[FrozenJsonObject, ...]
    unavailable: tuple[FrozenJsonObject, ...]
    ineligible: tuple[FrozenJsonObject, ...]
    registry_projection: Optional[FrozenJsonObject]
    decision: FrozenJsonObject
    phase_plan: tuple[FrozenJsonObject, ...]
    mode: str


@dataclass(frozen=True)
class A4Projection:
    current_handoff_decisions: tuple[ExecutorStepDecision, ...] = ()
    specialist_selection: SpecialistSelectionProjection = SpecialistSelectionProjection()


EMPTY_A4_PROJECTION = A4Projection()

_ACTIVE_INVOCATION_STATUSES = frozenset(
    {"reserved", "dispatch-unknown", "running"}
)
_SPECIALIST_PUBLIC_FIELDS = frozenset(
    {
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
        "specialists_ineligible",
        "specialist_registry_projection",
        "specialists_decision",
        "specialists_phase_plan",
        "specialist_invocations",
    }
)


def _validate_task_profile(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "primary", "secondary", "confidence", "risk", "signals"
    }:
        raise A4ProjectionError("specialist-task-profile-invalid")
    if (
        not isinstance(value["primary"], str)
        or not value["primary"]
        or not isinstance(value["secondary"], list)
        or any(not isinstance(item, str) or not item for item in value["secondary"])
        or type(value["confidence"]) not in {int, float}
        or not math.isfinite(value["confidence"])
        or not 0 <= value["confidence"] <= 1
        or value["risk"] not in {"low", "medium", "high"}
        or not isinstance(value["signals"], list)
        or any(not isinstance(item, str) or not item for item in value["signals"])
        or any(
            "/Users/" in item or item.startswith("/")
            for item in (
                value["primary"], *value["secondary"], *value["signals"]
            )
        )
    ):
        raise A4ProjectionError("specialist-task-profile-invalid")


def _validate_specialist_public_projection(document: Mapping[str, object]) -> None:
    probe = {
        key: document[key]
        for key in _SPECIALIST_PUBLIC_FIELDS
        if key in document
    }
    try:
        validate_specialist_public_state(probe)
    except SpecialistPublicContractError as exc:
        # A malformed collection is an A4 decode failure.  A nested unsafe
        # provider record must retain its structured public-contract path for
        # the legacy adapter's safe error translation.
        if exc.field_path.count("/") == 1:
            raise A4ProjectionError("specialist-selection-invalid") from exc
        raise


def _frozen_object(value: object, code: str) -> FrozenJsonObject:
    frozen = freeze_json_value(value)
    if not isinstance(frozen, FrozenJsonObject):
        raise A4ProjectionError(code)
    return frozen


def _optional_object(value: object, code: str) -> Optional[FrozenJsonObject]:
    return None if value is None else _frozen_object(value, code)


def _object_tuple(value: object, code: str) -> tuple[FrozenJsonObject, ...]:
    if not isinstance(value, list):
        raise A4ProjectionError(code)
    return tuple(_frozen_object(item, code) for item in value)


def _decode_specialist_selection(
    document: Mapping[str, object]
) -> SpecialistSelectionProjection:
    _validate_specialist_public_projection(document)
    invocations = document.get("specialist_invocations", [])
    if not isinstance(invocations, list):
        raise A4ProjectionError("specialist-invocations-invalid")
    active_ids = []
    for item in invocations:
        if not isinstance(item, Mapping):
            raise A4ProjectionError("specialist-invocations-invalid")
        if item.get("status") not in _ACTIVE_INVOCATION_STATUSES:
            continue
        invocation_id = item.get("invocation_id")
        if not isinstance(invocation_id, str) or not invocation_id:
            raise A4ProjectionError("specialist-invocations-invalid")
        active_ids.append(invocation_id)
    if len(active_ids) != len(set(active_ids)):
        raise A4ProjectionError("specialist-invocations-invalid")
    policy = document.get("planning_policy_version")
    if policy is not None and (type(policy) is not int or policy not in {0, 1}):
        raise A4ProjectionError("specialist-selection-invalid")
    mode = document.get("specialists_mode")
    strategy = document.get("planning_strategy")
    contract_digest = document.get("planning_contract_digest")
    for value in (mode, strategy, contract_digest):
        if value is not None and (not isinstance(value, str) or not value):
            raise A4ProjectionError("specialist-selection-invalid")
    return SpecialistSelectionProjection(
        task_profile=_optional_object(
            document.get("task_profile"), "specialist-selection-invalid"
        ),
        candidates=_object_tuple(
            document.get("specialists_candidates", []),
            "specialist-selection-invalid",
        ),
        selected=_object_tuple(
            document.get("specialists_selected", []),
            "specialist-selection-invalid",
        ),
        unavailable=_object_tuple(
            document.get("specialists_unavailable", []),
            "specialist-selection-invalid",
        ),
        ineligible=_object_tuple(
            document.get("specialists_ineligible", []),
            "specialist-selection-invalid",
        ),
        registry_projection=_optional_object(
            document.get("specialist_registry_projection"),
            "specialist-selection-invalid",
        ),
        decision=_optional_object(
            document.get("specialists_decision"),
            "specialist-selection-invalid",
        ),
        phase_plan=_object_tuple(
            document.get("specialists_phase_plan", []),
            "specialist-selection-invalid",
        ),
        mode=mode,
        active_provider_invocation_ids=tuple(active_ids),
        planning_policy_version=policy,
        planning_strategy=strategy,
        planning_contract_digest=contract_digest,
        planning_provider_binding=_optional_object(
            document.get("planning_provider_binding"),
            "specialist-selection-invalid",
        ),
    )


def specialist_recommendation_projection(
    result: object,
) -> SpecialistRecommendationProjection:
    if not isinstance(result, Mapping):
        raise A4ProjectionError("specialist-recommendation-invalid")
    _validate_specialist_public_projection(result)
    task_profile = _optional_object(
        result.get("task_profile"), "specialist-recommendation-invalid"
    )
    decision = _optional_object(
        result.get("specialists_decision"),
        "specialist-recommendation-invalid",
    )
    if task_profile is None or decision is None:
        raise A4ProjectionError("specialist-recommendation-invalid")
    _validate_task_profile(task_profile.thaw())
    prompted = decision.thaw().get("prompted_user")
    if type(prompted) is not bool:
        raise A4ProjectionError("specialist-recommendation-invalid")
    return SpecialistRecommendationProjection(
        task_profile=task_profile,
        candidates=_object_tuple(
            result.get("specialists_candidates"),
            "specialist-recommendation-invalid",
        ),
        selected=_object_tuple(
            result.get("specialists_selected"),
            "specialist-recommendation-invalid",
        ),
        unavailable=_object_tuple(
            result.get("specialists_unavailable"),
            "specialist-recommendation-invalid",
        ),
        ineligible=_object_tuple(
            result.get("specialists_ineligible"),
            "specialist-recommendation-invalid",
        ),
        registry_projection=_optional_object(
            result.get("specialist_registry_projection"),
            "specialist-recommendation-invalid",
        ),
        decision=decision,
        phase_plan=_object_tuple(
            result.get("specialists_phase_plan"),
            "specialist-recommendation-invalid",
        ),
        mode="interactive" if prompted else "auto",
    )


def validate_specialist_recommendation_shape(
    projection: object,
) -> None:
    """Close runtime dataclass fields before reducers inspect their contents."""
    if not isinstance(projection, SpecialistRecommendationProjection):
        raise A4ProjectionError("specialist-recommendation-invalid")
    object_sequences = (
        projection.candidates,
        projection.selected,
        projection.unavailable,
        projection.ineligible,
        projection.phase_plan,
    )
    if (
        not isinstance(projection.task_profile, FrozenJsonObject)
        or not isinstance(projection.decision, FrozenJsonObject)
        or any(
            type(sequence) is not tuple
            or any(not isinstance(item, FrozenJsonObject) for item in sequence)
            for sequence in object_sequences
        )
        or (
            projection.registry_projection is not None
            and not isinstance(
                projection.registry_projection, FrozenJsonObject
            )
        )
        or projection.mode not in {"auto", "interactive"}
    ):
        raise A4ProjectionError("specialist-recommendation-invalid")
    _validate_task_profile(projection.task_profile.thaw())
    prompted = projection.decision.thaw().get("prompted_user")
    if type(prompted) is not bool or projection.mode != (
        "interactive" if prompted else "auto"
    ):
        raise A4ProjectionError("specialist-recommendation-invalid")


def validate_specialist_recommendation_projection(
    projection: object,
) -> None:
    """Revalidate direct typed commands at the reducer trust boundary."""
    validate_specialist_recommendation_shape(projection)
    assert isinstance(projection, SpecialistRecommendationProjection)
    probe = {
        "specialists_candidates": [
            item.thaw() for item in projection.candidates
        ],
        "specialists_selected": [
            item.thaw() for item in projection.selected
        ],
        "specialists_unavailable": [
            item.thaw() for item in projection.unavailable
        ],
        "specialists_ineligible": [
            item.thaw() for item in projection.ineligible
        ],
        "specialist_registry_projection": (
            None
            if projection.registry_projection is None
            else projection.registry_projection.thaw()
        ),
        "specialists_decision": projection.decision.thaw(),
        "specialists_phase_plan": [
            item.thaw() for item in projection.phase_plan
        ],
        "specialist_invocations": [],
    }
    _validate_specialist_public_projection(probe)
    try:
        validate_specialist_lifecycle(probe, allow_pending=True)
    except SpecialistLifecycleError as exc:
        raise A4ProjectionError("specialist-selection-invalid") from exc


def _decision(value: object) -> ExecutorStepDecision:
    if not isinstance(value, Mapping) or set(value) != _DECISION_FIELDS:
        raise A4ProjectionError("executor-handoff-decisions-invalid")
    if (
        any(not isinstance(value.get(field), str) or not value[field] for field in (
            "handoff_id",
            "plan_digest",
            "plan_source",
            "source_id",
            "selection_source",
            "step_id",
        ))
        or type(value.get("plan_generation")) is not int
        or value["plan_generation"] < 1
        or type(value.get("iteration")) is not int
        or value["iteration"] < 0
        or value.get("result") not in {"ok", "partial", "failed"}
    ):
        raise A4ProjectionError("executor-handoff-decisions-invalid")
    return ExecutorStepDecision(
        handoff_id=value["handoff_id"],
        plan_digest=value["plan_digest"],
        plan_generation=value["plan_generation"],
        plan_source=value["plan_source"],
        source_id=value["source_id"],
        selection_source=value["selection_source"],
        iteration=value["iteration"],
        step_id=value["step_id"],
        result=value["result"],
    )


def decode_v4_a4_projection(document: Mapping[str, object], handoff: object) -> A4Projection:
    handoff_id = getattr(handoff, "handoff_id", None)
    raw_decisions = document.get("decisions", [])
    if not isinstance(raw_decisions, list):
        raise A4ProjectionError("executor-handoff-decisions-invalid")
    current = (
        ()
        if handoff_id is None
        else tuple(
            _decision(item)
            for item in raw_decisions
            if isinstance(item, Mapping)
            and item.get("handoff_id") == handoff_id
        )
    )
    if len({item.step_id for item in current}) != len(current):
        raise A4ProjectionError("executor-handoff-decisions-invalid")
    return A4Projection(
        current_handoff_decisions=current,
        specialist_selection=_decode_specialist_selection(document),
    )


def _decision_json(value: ExecutorStepDecision) -> dict[str, object]:
    return {
        "handoff_id": value.handoff_id,
        "plan_digest": value.plan_digest,
        "plan_generation": value.plan_generation,
        "plan_source": value.plan_source,
        "source_id": value.source_id,
        "selection_source": value.selection_source,
        "iteration": value.iteration,
        "step_id": value.step_id,
        "result": value.result,
    }


def project_v4_a4(
    document: dict[str, object], projection: A4Projection, handoff: object
) -> None:
    handoff_id = getattr(handoff, "handoff_id", None)
    existing = document.get("decisions", [])
    if not isinstance(existing, list):
        raise A4ProjectionError("executor-handoff-decisions-invalid")
    historical = (
        list(existing)
        if handoff_id is None
        else [
            item
            for item in existing
            if not isinstance(item, Mapping)
            or item.get("handoff_id") != handoff_id
        ]
    )
    current = [_decision_json(item) for item in projection.current_handoff_decisions]
    if historical or current or "decisions" in document:
        document["decisions"] = [*historical, *current]
    selection = projection.specialist_selection
    object_fields = {
        "task_profile": selection.task_profile,
        "specialist_registry_projection": selection.registry_projection,
        "specialists_decision": selection.decision,
        "planning_provider_binding": selection.planning_provider_binding,
    }
    for key, value in object_fields.items():
        if value is None:
            if key == "planning_provider_binding":
                document.pop(key, None)
            elif key in document:
                document[key] = None
        else:
            document[key] = value.thaw()
    list_fields = {
        "specialists_candidates": selection.candidates,
        "specialists_selected": selection.selected,
        "specialists_unavailable": selection.unavailable,
        "specialists_ineligible": selection.ineligible,
        "specialists_phase_plan": selection.phase_plan,
    }
    for key, values in list_fields.items():
        if values or key in document:
            document[key] = [value.thaw() for value in values]
    scalar_fields = {
        "specialists_mode": selection.mode,
        "planning_policy_version": selection.planning_policy_version,
        "planning_strategy": selection.planning_strategy,
        "planning_contract_digest": selection.planning_contract_digest,
    }
    for key, value in scalar_fields.items():
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
