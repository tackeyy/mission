"""Pure planning lifecycle and executor-handoff guards (#398)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from plan_contract import PlanContractError, canonical_plan_bytes


class PlanningLifecycleError(ValueError):
    pass


def _terminal(record: Mapping[str, Any]) -> bool:
    return record.get("status") not in {"selected", "reserved", "started", "running"}


def derive_planning_lifecycle(state: Mapping[str, Any]) -> dict[str, Any]:
    """Derive planning progress without writing duplicated provider status."""
    if state.get("planning_policy_version") != 1:
        return {"mode": "legacy-core", "next_action": "run-planner"}
    if state.get("phase") != "planning":
        return {"mode": "policy-v1", "next_action": None}
    iteration = state.get("iteration", 1)
    invocations = state.get("specialist_invocations") or []
    planning = [r for r in invocations if isinstance(r, Mapping) and r.get("phase") == "planning" and r.get("iteration") == iteration]
    if any(r.get("status") == "running" for r in planning):
        return {"mode": "policy-v1", "next_action": "reconcile-provider-invocation"}
    strategy = state.get("planning_strategy") or "core"
    if strategy == "provider-primary":
        binding = state.get("planning_provider_binding")
        selected = state.get("specialists_selected") or []
        if not isinstance(binding, Mapping) or not any(
            isinstance(item, Mapping)
            and item.get("planning_mode") == "primary"
            and item.get("provider_id") == binding.get("provider_id")
            and item.get("selection_id") == binding.get("selection_id")
            and item.get("planning_contract_digest") == binding.get("planning_contract_digest")
            for item in selected
        ):
            return {"mode": "policy-v1", "next_action": "run-planner"}
    canonical = state.get("canonical_plan")
    if isinstance(canonical, Mapping):
        return {"mode": "policy-v1", "next_action": "run-executor"}
    if strategy == "core":
        return {"mode": "policy-v1", "next_action": "run-planner"}
    current = planning[-1] if planning else None
    if current is None:
        return {"mode": "policy-v1", "next_action": "prepare-planning-provider"}
    status = current.get("status")
    if status == "reserved":
        return {"mode": "policy-v1", "next_action": "await-planning-approval"}
    if status in {"selected", "started"}:
        return {"mode": "policy-v1", "next_action": "invoke-planning-provider"}
    if status == "completed":
        imports = state.get("provider_plan_imports") or {}
        if current.get("invocation_id") not in imports:
            return {"mode": "policy-v1", "next_action": "import-planning-result"}
        return {"mode": "policy-v1", "next_action": "promote-canonical-plan" if strategy == "provider-primary" else "run-planner-with-evidence"}
    required = bool(current.get("required")) or state.get("planning_provider_required") is True
    return {"mode": "policy-v1", "next_action": "halt-required-planning-provider" if required else "run-planner", "degraded": not required}


def canonical_plan_identity(cwd: Path, plan: Mapping[str, Any], *, expected: Mapping[str, Any] | None = None, reader=None) -> tuple[bytes, list[str]]:
    """Re-read a canonical plan and bind bytes plus ordered step identifiers."""
    path = plan.get("path")
    digest = plan.get("digest")
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise PlanningLifecycleError("canonical-plan-path-invalid")
    candidate = cwd / path
    try:
        raw = reader(candidate) if reader is not None else candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise PlanningLifecycleError("canonical-plan-file-invalid") from exc
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise PlanningLifecycleError("canonical-plan-digest-drift")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningLifecycleError("canonical-plan-json-invalid") from exc
    if canonical_plan_bytes(payload) != raw:
        raise PlanningLifecycleError("canonical-plan-not-canonical")
    if not isinstance(payload, dict) or payload.get("schema") != "mission-plan/1":
        raise PlanningLifecycleError("canonical-plan-schema-invalid")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanningLifecycleError("canonical-plan-steps-invalid")
    ids = [step.get("id") for step in steps if isinstance(step, dict)]
    if len(ids) != len(steps) or not all(isinstance(step_id, str) and step_id for step_id in ids) or len(set(ids)) != len(ids):
        raise PlanningLifecycleError("canonical-plan-step-ids-invalid")
    for key in ("generation", "source", "source_id", "selection_source", "iteration"):
        if expected is not None and plan.get(key) != expected.get(key):
            raise PlanningLifecycleError(f"canonical-plan-{key}-mismatch")
    return raw, ids


def validate_handoff_step(state: Mapping[str, Any], step_id: str) -> dict[str, Any]:
    handoff = state.get("executor_handoff")
    plan = state.get("canonical_plan")
    if not isinstance(handoff, Mapping) or not isinstance(plan, Mapping):
        raise PlanningLifecycleError("executor-handoff-missing")
    if handoff.get("status") not in {"prepared", "consuming"}:
        raise PlanningLifecycleError("executor-handoff-not-active")
    allowed = handoff.get("step_ids")
    if not isinstance(allowed, list) or step_id not in allowed:
        raise PlanningLifecycleError("executor-step-not-member")
    return dict(handoff)
