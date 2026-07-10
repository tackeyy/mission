"""Shared specialist candidate accounting rules for mission state and audits."""

from __future__ import annotations

from typing import Any


TERMINAL_SPECIALIST_INVOCATION_STATUSES = {
    "completed",
    "prepared",
    "awaiting-input",
    "inline-applied",
    "skill-tool-applied",
    "skipped",
    "unavailable",
    "failed",
}

APPLIED_SPECIALIST_INVOCATION_STATUSES = {
    "completed",
    "inline-applied",
    "skill-tool-applied",
}

HIGH_RISK_PROFILES = {"security", "testing", "infra"}
AUDIT_ACCOUNTING_PROFILES = {"security", "testing", "risk"}
AUDIT_MISSION_SIGNALS = {
    "audit",
    "auditing",
    "execution-log",
    "health-check",
    "improvement",
    "log review",
    "self-improvement",
    "selfheal",
    "監査",
    "改善",
    "実行ログ",
}
DATABASE_STRONG_SIGNALS = {"database", "schema", "migration", "query", "sql", "persistence"}


def explicitly_selected_specialist_skills(state: dict[str, Any]) -> set[str]:
    skills: set[str] = set()
    for selected in state.get("specialists_selected") or []:
        if isinstance(selected, dict) and selected.get("skill"):
            skills.add(str(selected["skill"]))
    return skills


def selected_specialist_skills(state: dict[str, Any]) -> set[str]:
    skills = explicitly_selected_specialist_skills(state)
    for phase in state.get("specialists_phase_plan") or []:
        if not isinstance(phase, dict):
            continue
        for provider in phase.get("providers") or []:
            if provider:
                skills.add(str(provider))
    return skills


def terminal_invoked_specialist_skills(state: dict[str, Any]) -> set[str]:
    skills: set[str] = set()
    for invocation in state.get("specialist_invocations") or []:
        if not isinstance(invocation, dict):
            continue
        skill = invocation.get("skill")
        status = invocation.get("status")
        if skill and status in TERMINAL_SPECIALIST_INVOCATION_STATUSES:
            skills.add(str(skill))
    return skills


def applied_specialist_invocation_skills(state: dict[str, Any]) -> set[str]:
    skills: set[str] = set()
    for invocation in state.get("specialist_invocations") or []:
        if not isinstance(invocation, dict):
            continue
        skill = invocation.get("skill")
        status = invocation.get("status")
        if skill and status in APPLIED_SPECIALIST_INVOCATION_STATUSES:
            skills.add(str(skill))
    return skills


def _candidate_skill(candidate: dict[str, Any]) -> str:
    return str(candidate.get("skill") or candidate.get("role") or candidate.get("name") or "")


def available_specialist_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in state.get("specialists_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        skill = _candidate_skill(candidate)
        if not skill or skill in seen:
            continue
        status = str(candidate.get("status") or "").lower()
        if status in {"missing", "unavailable"}:
            continue
        if candidate.get("available") is False or candidate.get("installed") is False:
            continue
        seen.add(skill)
        candidates.append(candidate)
    return candidates


def candidate_specialist_skills(state: dict[str, Any]) -> list[str]:
    return [_candidate_skill(candidate) for candidate in available_specialist_candidates(state)]


def _task_profiles(state: dict[str, Any]) -> set[str]:
    task_profile = state.get("task_profile") if isinstance(state.get("task_profile"), dict) else {}
    primary = str(task_profile.get("primary") or "").lower()
    secondary = {str(value).lower() for value in task_profile.get("secondary") or []}
    return {primary, *secondary} - {""}


def _candidate_profiles(candidate: dict[str, Any]) -> set[str]:
    profiles = candidate.get("task_profiles") or candidate.get("profiles") or []
    if isinstance(profiles, str):
        profiles = [profiles]
    return {str(value).lower() for value in profiles}


def _database_strong_signal_present(state: dict[str, Any]) -> bool:
    task_profile = state.get("task_profile") if isinstance(state.get("task_profile"), dict) else {}
    values: list[str] = [
        str(state.get("mission") or ""),
        " ".join(str(value) for value in state.get("planned_files") or []),
        " ".join(str(value) for value in task_profile.get("signals") or []),
    ]
    haystack = " ".join(values).lower()
    return any(signal in haystack for signal in DATABASE_STRONG_SIGNALS)


def _audit_or_improvement_signal_present(state: dict[str, Any]) -> bool:
    task_profile = state.get("task_profile") if isinstance(state.get("task_profile"), dict) else {}
    values: list[str] = [
        str(state.get("mission") or ""),
        " ".join(str(value) for value in state.get("planned_files") or []),
        " ".join(str(value) for value in task_profile.get("signals") or []),
    ]
    haystack = " ".join(values).lower()
    return any(signal in haystack for signal in AUDIT_MISSION_SIGNALS)


def candidate_accounting_required(state: dict[str, Any], candidate: dict[str, Any] | None = None) -> bool:
    """Return true when a candidate needs an explicit terminal decision trail.

    The default stays hackable: optional providers are evidence sources, not hard
    gates. Critical/high-risk runs account for all available candidates. Complex
    runs account only for candidates tied to risk-bearing profiles.
    """
    task_profile = state.get("task_profile") if isinstance(state.get("task_profile"), dict) else {}
    complexity = str(state.get("complexity") or "Unknown")
    risk = str(task_profile.get("risk") or "").lower()

    if candidate and candidate.get("required"):
        return True
    if complexity == "Critical" or risk == "high":
        return True
    if candidate is None:
        return False

    profiles = _candidate_profiles(candidate)
    if (
        complexity in {"Standard", "Complex"}
        and _audit_or_improvement_signal_present(state)
        and profiles & AUDIT_ACCOUNTING_PROFILES
    ):
        return True
    if complexity != "Complex":
        return False

    if profiles & HIGH_RISK_PROFILES:
        return True
    if "database" in profiles and _database_strong_signal_present(state):
        return True
    return False


def candidate_accounting_report(state: dict[str, Any]) -> dict[str, Any]:
    selected = selected_specialist_skills(state)
    terminal = terminal_invoked_specialist_skills(state)
    applied = applied_specialist_invocation_skills(state)
    any_decision_trail = bool(selected or terminal)
    accounted = selected | terminal
    unaccounted: list[dict[str, Any]] = []
    required_unaccounted: list[dict[str, Any]] = []
    result_required_unmet: list[dict[str, Any]] = []

    for candidate in available_specialist_candidates(state):
        skill = _candidate_skill(candidate)
        if candidate.get("required") and skill not in applied:
            result_required_unmet.append({
                "role": candidate.get("role") or skill,
                "skill": skill,
                "kind": candidate.get("kind", "skill"),
                "profiles": sorted(_candidate_profiles(candidate)),
                "reason": "required provider has no applied/completed evidence",
                "required": True,
                "requires_result": True,
            })
        if skill in accounted:
            continue
        requires_accounting = candidate_accounting_required(state, candidate)
        if requires_accounting or not any_decision_trail:
            item = {
                "role": candidate.get("role") or skill,
                "skill": skill,
                "kind": candidate.get("kind", "skill"),
                "profiles": sorted(_candidate_profiles(candidate)),
                "reason": candidate.get("reason") or "",
                "required": bool(candidate.get("required")),
                "requires_accounting": requires_accounting,
            }
            unaccounted.append(item)
            if requires_accounting:
                required_unaccounted.append(item)

    priority = "P1" if (required_unaccounted or result_required_unmet) else ("P2" if unaccounted else None)
    return {
        "accounting_required": bool(required_unaccounted),
        "result_required": bool(result_required_unmet),
        "priority": priority,
        "selected_skills": sorted(selected),
        "terminal_invocation_skills": sorted(terminal),
        "applied_invocation_skills": sorted(applied),
        "unaccounted_candidates": unaccounted,
        "required_unaccounted_candidates": required_unaccounted,
        "result_required_unmet_candidates": result_required_unmet,
    }
