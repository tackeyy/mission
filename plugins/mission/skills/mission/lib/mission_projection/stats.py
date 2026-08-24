"""Pure stats reduction over already-collected mission observations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Mapping, Protocol, Sequence


REVIEW_PROSE_BYTES_WARN = 20_000
REVIEW_PROSE_RATIO_WARN = 0.7
SCORE_MIN = 0.0
SCORE_MAX = 5.0


class StatsSnapshot(Protocol):
    classification: str
    passes: bool
    phase: str
    session_role: str
    artifact_terminal_outcome: str | None


@dataclass(frozen=True)
class StatsProjectionInput:
    """Inputs collected before entering the pure stats projection boundary."""

    states: Sequence[Mapping[str, object]]
    snapshots: Sequence[StatsSnapshot]
    pass_rate_summary: Mapping[str, object]
    duplicate_state_group_count: int
    state_read_error_count: int
    bounded_context_observations: Sequence[tuple[bool, bool]]
    score_provenance_counts: Mapping[str, int]
    command_outcome_counts: Mapping[str, int]
    duration_observations: Sequence[float]
    artifact_coverage: Mapping[str, object]
    activity_timing: Mapping[str, object]
    planning_provider_kpis: Mapping[str, object]
    failure_ledger_counts: Mapping[str, object]
    iteration_recovery: Mapping[str, object]


def _finite_score(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and SCORE_MIN <= float(value) <= SCORE_MAX
    )


def _finite_nonnegative_phase_seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )


def _nearest_rank_percentile(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _reviewer_output_stats(states: Sequence[Mapping[str, object]]) -> dict:
    records = []
    for state in states:
        state_records = state.get("reviewer_output_records", [])
        if not isinstance(state_records, list):
            continue
        for record in state_records:
            if not isinstance(record, dict):
                continue
            prose_bytes = record.get("prose_bytes")
            prose_ratio = record.get("prose_ratio")
            if (
                not isinstance(prose_bytes, int)
                or isinstance(prose_bytes, bool)
                or prose_bytes < 0
                or not isinstance(prose_ratio, (int, float))
                or isinstance(prose_ratio, bool)
                or not 0 <= float(prose_ratio) <= 1
            ):
                continue
            records.append((prose_bytes, float(prose_ratio)))
    prose_values = [prose_bytes for prose_bytes, _ratio in records]
    return {
        "records": len(records),
        "oversize_warns": sum(
            1
            for prose_bytes, prose_ratio in records
            if prose_bytes > REVIEW_PROSE_BYTES_WARN
            or prose_ratio > REVIEW_PROSE_RATIO_WARN
        ),
        "prose_bytes_p50": _nearest_rank_percentile(prose_values, 0.5),
        "prose_bytes_p90": _nearest_rank_percentile(prose_values, 0.9),
    }


def _latest_composite(history: object) -> float | None:
    for entry in reversed(history):
        composite = entry.get("composite")
        if _finite_score(composite):
            return composite
    return None


def _build_agent_summary(
    states: Sequence[Mapping[str, object]], classes: Sequence[str]
) -> dict:
    by_agent: dict = {}
    for state, classification in zip(states, classes):
        agent = state.get("agent") or "unknown"
        bucket = by_agent.setdefault(
            agent,
            {
                "total": 0,
                "pass": 0,
                "halt": 0,
                "incomplete": 0,
                "abandoned": 0,
            },
        )
        bucket["total"] += 1
        bucket[classification] += 1
    return by_agent


def _build_breakdown(
    states: Sequence[Mapping[str, object]],
    classes: Sequence[str],
    keys: Sequence[object],
) -> dict:
    output: dict = {}
    for _state, classification, key in zip(states, classes, keys):
        normalized_key = key or "unknown"
        bucket = output.setdefault(
            normalized_key,
            {
                "total": 0,
                "pass": 0,
                "halt": 0,
                "incomplete": 0,
                "abandoned": 0,
            },
        )
        bucket["total"] += 1
        bucket[classification] = bucket.get(classification, 0) + 1
    return output


def _project_name(state: Mapping[str, object]) -> str:
    path = (state.get("project_root") or "unknown").rstrip("/")
    return path.rsplit("/", 1)[-1] or "unknown"


def _build_halt_category_breakdown(
    states: Sequence[Mapping[str, object]], classes: Sequence[str]
) -> dict:
    output: dict = {}
    for state, classification in zip(states, classes):
        if classification != "halt":
            continue
        if "halt_category" not in state or state.get("halt_category") == "":
            category = "unknown"
        elif isinstance(state.get("halt_category"), str):
            category = state["halt_category"]
        else:
            category = json.dumps(
                state.get("halt_category"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        output[category] = output.get(category, 0) + 1
    return dict(sorted(output.items()))


def _iteration_bucket(iteration: object) -> str:
    if isinstance(iteration, int) and iteration <= 3:
        return str(iteration)
    if isinstance(iteration, int):
        return "4+"
    return "unknown"


def _build_iteration_by_review_tier(
    states: Sequence[Mapping[str, object]],
) -> dict:
    output: dict = {}
    for state in states:
        tier = state.get("review_tier") or "unknown"
        bucket = _iteration_bucket(state.get("iteration", 0))
        histogram = output.setdefault(tier, {})
        histogram[bucket] = histogram.get(bucket, 0) + 1
    return output


def _phase_duration_totals(states: Sequence[Mapping[str, object]]) -> dict:
    totals: dict = {}
    for state in states:
        durations = state.get("phase_durations_sec")
        if not isinstance(durations, dict):
            continue
        for phase, seconds in durations.items():
            if not isinstance(phase, str):
                continue
            value = _finite_nonnegative_phase_seconds(seconds)
            if value is None:
                continue
            updated = _finite_nonnegative_phase_seconds(
                totals.get(phase, 0.0) + value
            )
            if updated is not None:
                totals[phase] = updated
    return dict(sorted(totals.items()))


def _artifact_lint_counts(states: Sequence[Mapping[str, object]]) -> dict:
    counts = {
        "empty_section": 0,
        "stub_forward_reference": 0,
        "clean": 0,
    }
    for state in states:
        lint = state.get("artifact_lint")
        if not isinstance(lint, list):
            continue
        if not lint:
            counts["clean"] += 1
            continue
        for finding in lint:
            if not isinstance(finding, dict):
                continue
            if finding.get("kind") == "empty-section":
                counts["empty_section"] += 1
            elif finding.get("kind") == "stub-forward-reference":
                counts["stub_forward_reference"] += 1
    return counts


def _bounded_context_counts(
    observations: Sequence[tuple[bool, bool]],
) -> dict[str, int]:
    counts = {
        "expected_bounded": 0,
        "manifest_generated": 0,
        "fallback_full": 0,
    }
    for expected_bounded, generated in observations:
        if expected_bounded:
            counts["expected_bounded"] += 1
        if generated:
            counts["manifest_generated"] += 1
        if expected_bounded and not generated:
            counts["fallback_full"] += 1
    return counts


def project_stats(request: StatsProjectionInput) -> dict:
    """Reduce collected observations without reading external state."""
    states = request.states
    snapshots = request.snapshots
    pass_rate_summary = request.pass_rate_summary
    count = len(states)
    if count == 0:
        return {
            "total_sessions": 0,
            "pass_count": 0,
            "halt_count": 0,
            "state_read_error_count": request.state_read_error_count,
            "duplicate_state_group_count": request.duplicate_state_group_count,
            "incomplete_count": 0,
            "abandoned_count": 0,
            "active_count": 0,
            "active_no_score_count": 0,
            "stale_count": 0,
            "raw_pass_rate_numerator": 0,
            "raw_pass_rate_denominator": 0,
            "raw_pass_rate": None,
            "completed_pass_rate_numerator": 0,
            "completed_pass_rate_denominator": 0,
            "completed_pass_rate": None,
            "terminal_outcome_counts": pass_rate_summary["terminal_outcome_counts"],
            "terminal_count": 0,
            "non_terminal_count": 0,
            "role_counts": pass_rate_summary["role_counts"],
            "implementer_pass_rate_numerator": 0,
            "implementer_pass_rate_denominator": 0,
            "implementer_pass_rate": None,
            "evidence_completion_rate_numerator": 0,
            "evidence_completion_rate_denominator": 0,
            "evidence_completion_rate": None,
            "pass_rate_numerator": 0,
            "pass_rate_denominator": 0,
            "pass_rate": None,
            "forced_pass_count": 0,
            "forced_pass_rate": None,
            "ungated_pass_count": 0,
            "ungated_pass_rate": None,
            "avg_iterations": None,
            "avg_final_composite": None,
            "avg_session_duration_sec": None,
            "median_session_duration_sec": None,
            "phase_duration_totals_sec": {},
            "phase_duration_avg_sec": {},
            "by_agent": {},
            "by_project": {},
            "by_complexity": {},
            "iteration_histogram": {},
            "by_review_tier": {},
            "iteration_by_review_tier": {},
            "by_cli_version": {},
            "by_halt_category": {},
            "parallel_review_counts": {"true": 0, "false": 0, "unknown": 0},
            "artifact_lint_counts": _artifact_lint_counts([]),
            "artifact_coverage": request.artifact_coverage,
            "bounded_context_counts": _bounded_context_counts([]),
            "reviewer_output_stats": _reviewer_output_stats([]),
            "score_provenance_counts": request.score_provenance_counts,
            "command_outcome_counts": request.command_outcome_counts,
            "activity_timing": request.activity_timing,
            "planning_provider_kpis": request.planning_provider_kpis,
            "failure_ledger_counts": request.failure_ledger_counts,
            "iteration_recovery": request.iteration_recovery,
        }

    classes = [snapshot.classification for snapshot in snapshots]
    pass_count = classes.count("pass")
    forced_pass_count = sum(
        1
        for state, snapshot in zip(states, snapshots)
        if snapshot.passes and state.get("passes_forced")
    )
    ungated_pass_count = sum(
        1
        for state, snapshot in zip(states, snapshots)
        if snapshot.passes
        and _latest_composite(state.get("score_history", [])) is None
        and not state.get("passes_forced")
        and not state.get("force_reason")
    )
    parallel_review_counts = {"true": 0, "false": 0, "unknown": 0}
    for state in states:
        value = state.get("last_parallel_execution")
        if value is True:
            parallel_review_counts["true"] += 1
        elif value is False:
            parallel_review_counts["false"] += 1
        elif value == "unknown":
            parallel_review_counts["unknown"] += 1

    iterations = [state.get("iteration", 0) for state in states]
    final_composites = [
        composite
        for composite in (
            _latest_composite(state.get("score_history", [])) for state in states
        )
        if composite is not None
    ]
    durations = list(request.duration_observations)
    by_agent = _build_agent_summary(states, classes)
    by_project = _build_breakdown(
        states, classes, [_project_name(state) for state in states]
    )
    by_complexity = _build_breakdown(
        states, classes, [state.get("complexity") or "Unknown" for state in states]
    )
    by_review_tier = _build_breakdown(
        states, classes, [state.get("review_tier") or "unknown" for state in states]
    )
    by_cli_version = _build_breakdown(
        states, classes, [state.get("cli_version") or "unknown" for state in states]
    )
    phase_totals = _phase_duration_totals(states)
    iteration_histogram: dict = {}
    for iteration in iterations:
        bucket = _iteration_bucket(iteration)
        iteration_histogram[bucket] = iteration_histogram.get(bucket, 0) + 1

    return {
        "total_sessions": count,
        "state_read_error_count": request.state_read_error_count,
        "duplicate_state_group_count": request.duplicate_state_group_count,
        "pass_count": pass_count,
        "halt_count": pass_rate_summary["halt_count"],
        "incomplete_count": pass_rate_summary["incomplete_count"],
        "abandoned_count": pass_rate_summary["abandoned_count"],
        "active_count": pass_rate_summary["active_count"],
        "active_no_score_count": pass_rate_summary["active_no_score_count"],
        "stale_count": pass_rate_summary["stale_count"],
        "raw_pass_rate_numerator": pass_rate_summary["raw_pass_rate_numerator"],
        "raw_pass_rate_denominator": pass_rate_summary["raw_pass_rate_denominator"],
        "raw_pass_rate": pass_rate_summary["raw_pass_rate"],
        "completed_pass_rate_numerator": pass_rate_summary["completed_pass_rate_numerator"],
        "completed_pass_rate_denominator": pass_rate_summary["completed_pass_rate_denominator"],
        "completed_pass_rate": pass_rate_summary["completed_pass_rate"],
        "terminal_outcome_counts": pass_rate_summary["terminal_outcome_counts"],
        "terminal_count": pass_rate_summary["terminal_count"],
        "non_terminal_count": pass_rate_summary["non_terminal_count"],
        "role_counts": pass_rate_summary["role_counts"],
        "implementer_pass_rate_numerator": pass_rate_summary["implementer_pass_rate_numerator"],
        "implementer_pass_rate_denominator": pass_rate_summary["implementer_pass_rate_denominator"],
        "implementer_pass_rate": pass_rate_summary["implementer_pass_rate"],
        "evidence_completion_rate_numerator": pass_rate_summary["evidence_completion_rate_numerator"],
        "evidence_completion_rate_denominator": pass_rate_summary["evidence_completion_rate_denominator"],
        "evidence_completion_rate": pass_rate_summary["evidence_completion_rate"],
        "pass_rate_numerator": pass_rate_summary["raw_pass_rate_numerator"],
        "pass_rate_denominator": pass_rate_summary["raw_pass_rate_denominator"],
        "pass_rate": pass_rate_summary["raw_pass_rate"],
        "forced_pass_count": forced_pass_count,
        "parallel_review_counts": parallel_review_counts,
        "artifact_lint_counts": _artifact_lint_counts(states),
        "artifact_coverage": request.artifact_coverage,
        "bounded_context_counts": _bounded_context_counts(
            request.bounded_context_observations
        ),
        "reviewer_output_stats": _reviewer_output_stats(states),
        "score_provenance_counts": request.score_provenance_counts,
        "command_outcome_counts": request.command_outcome_counts,
        "forced_pass_rate": forced_pass_count / pass_count if pass_count else None,
        "ungated_pass_count": ungated_pass_count,
        "ungated_pass_rate": ungated_pass_count / pass_count if pass_count else None,
        "avg_iterations": sum(iterations) / count,
        "avg_final_composite": (
            sum(final_composites) / len(final_composites)
            if final_composites
            else None
        ),
        "avg_session_duration_sec": sum(durations) / len(durations) if durations else None,
        "median_session_duration_sec": _median(durations),
        "phase_duration_totals_sec": phase_totals,
        "phase_duration_avg_sec": {
            phase: total / count for phase, total in phase_totals.items()
        },
        "by_agent": by_agent,
        "by_project": by_project,
        "by_complexity": by_complexity,
        "iteration_histogram": iteration_histogram,
        "by_review_tier": by_review_tier,
        "iteration_by_review_tier": _build_iteration_by_review_tier(states),
        "by_cli_version": by_cli_version,
        "by_halt_category": _build_halt_category_breakdown(states, classes),
        "activity_timing": request.activity_timing,
        "planning_provider_kpis": request.planning_provider_kpis,
        "failure_ledger_counts": request.failure_ledger_counts,
        "iteration_recovery": request.iteration_recovery,
    }
