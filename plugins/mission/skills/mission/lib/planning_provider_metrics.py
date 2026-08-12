"""Pure, versioned planning-provider KPI reduction (#399)."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence


SCHEMA = "mission-planning-provider-kpi/1"
_POPULATIONS = {"observed", "controlled"}
_REASON_BUCKETS = ("fallback", "approval_wait", "invalid_plan", "preflight_rejection")
_KNOWN_REASONS = {
    "fallback": {"provider-unavailable", "timeout", "invalid-plan", "unvalidated-evidence", "unknown"},
    "approval_wait": {"awaiting-approval", "awaiting-input", "unknown"},
    "invalid_plan": {"invalid-plan", "binding-mismatch", "canonical-plan-digest-drift", "unknown"},
    "preflight_rejection": {"preflight-required", "preflight-not-consumed", "receipt-invalid", "unknown"},
}
_COUNTERS = ("ineligible_external_planning_invocations", "dry_run_external_effect_count", "legacy_session_retroactive_provider_invocations", "authority_injection_accept_count")
_RATES = ("eligible_complex_planning_selection", "preflight_live_digest_match", "canonical_plan_executor_lineage")


class PlanningProviderMetricError(ValueError):
    pass


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    if type(numerator) is not int or type(denominator) is not int or numerator < 0 or denominator < 0 or numerator > denominator:
        raise PlanningProviderMetricError("invalid numerator/denominator")
    return {"numerator": numerator, "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None}


def _reason(bucket: str, value: object) -> str:
    text = value if isinstance(value, str) else "unknown"
    return text if text in _KNOWN_REASONS[bucket] else "unknown"


def _cohort(state: Mapping[str, Any], population_kind: str) -> tuple[str, str, str, str, str]:
    profile = state.get("task_profile") if isinstance(state.get("task_profile"), Mapping) else {}
    return (
        str(state.get("complexity") or "Unknown"), str(profile.get("primary") or "general"),
        str(state.get("planning_strategy") or "legacy-core"),
        "required" if state.get("planning_provider_required") is True else "optional", population_kind,
    )


def validate_planning_provider_kpis(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        raise PlanningProviderMetricError("schema is invalid")
    population = value.get("population")
    if not isinstance(population, Mapping) or population.get("kind") not in _POPULATIONS or type(population.get("session_count")) is not int or population["session_count"] < 0:
        raise PlanningProviderMetricError("population is invalid")
    totals = value.get("totals")
    if not isinstance(totals, Mapping):
        raise PlanningProviderMetricError("totals are invalid")
    for key in _COUNTERS:
        if type(totals.get(key)) is not int or totals[key] < 0:
            raise PlanningProviderMetricError("counter is invalid")
    for key in _RATES:
        detail = totals.get(key)
        if not isinstance(detail, Mapping) or set(detail) != {"numerator", "denominator", "rate"}:
            raise PlanningProviderMetricError("rate is invalid")
        expected = _rate(detail["numerator"], detail["denominator"])
        if detail != expected:
            raise PlanningProviderMetricError("rate is inconsistent")


def reduce_planning_provider_kpis(
    states: Sequence[Mapping[str, Any]], *, population_kind: str
) -> dict[str, Any]:
    """Reduce already-deduplicated state snapshots without any I/O or re-reads."""
    if population_kind not in _POPULATIONS:
        raise PlanningProviderMetricError("population kind is invalid")
    totals = {"ineligible_external_planning_invocations": 0, "dry_run_external_effect_count": 0,
              "legacy_session_retroactive_provider_invocations": 0, "authority_injection_accept_count": 0}
    eligible = [0, 0]; preflight = [0, 0]; lineage = [0, 0]
    reasons = {bucket: Counter() for bucket in _REASON_BUCKETS}
    cohorts: dict[tuple[str, str, str, str, str], dict[str, Any]] = defaultdict(lambda: {"session_count": 0})
    for state in states:
        if not isinstance(state, Mapping):
            raise PlanningProviderMetricError("state must be an object")
        key = _cohort(state, population_kind); cohorts[key]["session_count"] += 1
        for counter in _COUNTERS:
            value = state.get(counter, 0)
            if type(value) is not int or value < 0:
                raise PlanningProviderMetricError("counter is invalid")
            totals[counter] += value
        policy_v1 = state.get("planning_policy_version") == 1
        invocations = [entry for entry in state.get("specialist_invocations") or [] if isinstance(entry, Mapping) and entry.get("phase") == "planning"]
        if not policy_v1:
            continue
        if state.get("complexity") in {"Complex", "Critical"}:
            eligible[1] += 1
            if state.get("planning_strategy") in {"provider-primary", "provider-advisory"}:
                eligible[0] += 1
        for invocation in invocations:
            if invocation.get("status") in {"reserved", "running", "completed"}:
                preflight[1] += 1
                pointers = state.get("provider_preflights") if isinstance(state.get("provider_preflights"), Mapping) else {}
                matched = [record for record in pointers.values() if isinstance(record, Mapping) and record.get("invocation_id") == invocation.get("invocation_id")]
                if len(matched) == 1 and matched[0].get("status") == "consumed":
                    preflight[0] += 1
            if invocation.get("status") in {"failed", "rejected", "unvalidated-evidence"}:
                reason = invocation.get("reason_code") or invocation.get("status")
                reasons["fallback"][_reason("fallback", reason)] += 1
                if reason == "preflight-required":
                    reasons["preflight_rejection"]["preflight-required"] += 1
                if reason == "invalid-plan":
                    reasons["invalid_plan"]["invalid-plan"] += 1
        for record in (state.get("provider_preflights") or {}).values() if isinstance(state.get("provider_preflights"), Mapping) else []:
            if isinstance(record, Mapping) and record.get("status") == "awaiting-approval":
                reasons["approval_wait"]["awaiting-approval"] += 1
        plan = state.get("canonical_plan") if isinstance(state.get("canonical_plan"), Mapping) else None
        handoff = state.get("executor_handoff") if isinstance(state.get("executor_handoff"), Mapping) else None
        for decision in state.get("decisions") or []:
            if not isinstance(decision, Mapping) or not decision.get("step_id"):
                continue
            lineage[1] += 1
            if plan and handoff and decision.get("plan_digest") == plan.get("digest") and decision.get("plan_generation") == plan.get("generation") and decision.get("handoff_id") == handoff.get("handoff_id"):
                lineage[0] += 1
    totals["eligible_complex_planning_selection"] = _rate(*eligible)
    totals["preflight_live_digest_match"] = _rate(*preflight)
    totals["canonical_plan_executor_lineage"] = _rate(*lineage)
    cohort_docs = [
        {"complexity": key[0], "task_profile": key[1], "planning_strategy": key[2], "requirement": key[3], "population_kind": key[4], **value}
        for key, value in sorted(cohorts.items())
    ]
    result = {"schema": SCHEMA, "population": {"kind": population_kind, "session_count": len(states)},
            "totals": totals, "reason_code_counts": {key: dict(sorted(value.items())) for key, value in reasons.items()}, "cohorts": cohort_docs}
    validate_planning_provider_kpis(result)
    return result
