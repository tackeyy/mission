"""Bounded activity timing state and shared aggregation for mission tools."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any


ACTIVITY_KINDS = {
    "active",
    "external-wait",
    "approval-wait",
    "reviewer-wait",
    "idle",
}
WAIT_KINDS = {"external-wait", "approval-wait", "reviewer-wait"}
ACTIVITY_REASONS_BY_KIND = {
    "active": {
        "work",
        "implementation",
        "planning",
        "review",
        "scoring",
        "resumed-implementation",
        "other",
    },
    "external-wait": {"external-response", "external-command", "other"},
    "approval-wait": {"user-approval", "policy-approval", "other"},
    "reviewer-wait": {"review-response", "independent-review", "other"},
    "idle": {"no-runnable-work", "interrupted", "other"},
}
TERMINAL_PHASES = {"done", "halted"}
RECENT_SEGMENT_LIMIT = 32
PERCENTILE_METHOD = "linear-interpolation-r7"
TASK_KEY_METHOD = "mission_id-or-unknown"


class ActivityTimingError(ValueError):
    """Invalid activity transition that must not mutate state."""


def _parse_at(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed(start: str | None, end: str | None) -> float | None:
    started = _parse_at(start)
    ended = _parse_at(end)
    if not started or not ended:
        return None
    seconds = (ended - started).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return float(seconds)


def _reject_state_clock_rollback(state: dict[str, Any], at: str) -> None:
    event = _parse_at(at)
    updated = _parse_at(state.get("updated_at"))
    if not event:
        raise ActivityTimingError("invalid activity timestamp")
    if updated and event < updated:
        raise ActivityTimingError("activity timestamp is before the last state update")


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def sanitize_activity_detail(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = " ".join(text.split())[:160].strip()
    return text or None


def validate_activity(kind: str, reason: str) -> None:
    if kind not in ACTIVITY_KINDS:
        raise ActivityTimingError(f"unknown activity kind: {kind}")
    if reason not in ACTIVITY_REASONS_BY_KIND[kind]:
        raise ActivityTimingError(
            f"unknown reason for {kind}: {reason}; allowed={sorted(ACTIVITY_REASONS_BY_KIND[kind])}"
        )


def _empty_rollup() -> dict[str, Any]:
    return {
        "observed_total_sec": 0.0,
        "closed_segment_count": 0,
        "activity_duration_totals_sec": {},
        "phase_activity_duration_totals_sec": {},
        "wait_reason_totals_sec": {},
    }


def _rollup(state: dict[str, Any]) -> dict[str, Any]:
    rollup = state.get("activity_rollup")
    if not isinstance(rollup, dict):
        rollup = _empty_rollup()
        state["activity_rollup"] = rollup
    defaults = _empty_rollup()
    for key, value in defaults.items():
        current = rollup.get(key)
        if isinstance(value, float):
            if _finite_nonnegative(current) is None:
                rollup[key] = value
        elif isinstance(value, int):
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                rollup[key] = value
        elif not isinstance(current, dict):
            rollup[key] = value
    return rollup


def _record_anomaly(state: dict[str, Any], code: str) -> None:
    counts = state.setdefault("activity_anomaly_counts", {})
    if not isinstance(counts, dict):
        counts = {}
        state["activity_anomaly_counts"] = counts
    current = counts.get(code)
    counts[code] = (
        current + 1
        if isinstance(current, int) and not isinstance(current, bool) and current >= 0
        else 1
    )


def _add_nested(mapping: dict[str, Any], key: str, nested_key: str, seconds: float) -> None:
    nested = mapping.setdefault(key, {})
    if not isinstance(nested, dict):
        nested = {}
        mapping[key] = nested
    current = _finite_nonnegative(nested.get(nested_key)) or 0.0
    nested[nested_key] = current + seconds


def _record_closed_segment(state: dict[str, Any], segment: dict[str, Any]) -> None:
    seconds = float(segment["duration_sec"])
    kind = segment["kind"]
    phase = segment["phase"]
    reason = segment["reason"]
    rollup = _rollup(state)
    rollup["observed_total_sec"] = (
        (_finite_nonnegative(rollup.get("observed_total_sec")) or 0.0) + seconds
    )
    count = rollup.get("closed_segment_count")
    rollup["closed_segment_count"] = (
        count + 1 if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 1
    )
    totals = rollup.setdefault("activity_duration_totals_sec", {})
    totals[kind] = (_finite_nonnegative(totals.get(kind)) or 0.0) + seconds
    phase_totals = rollup.setdefault("phase_activity_duration_totals_sec", {})
    _add_nested(phase_totals, phase, kind, seconds)
    if kind in WAIT_KINDS:
        reasons = rollup.setdefault("wait_reason_totals_sec", {})
        _add_nested(reasons, kind, reason or "unknown", seconds)
    recent = state.setdefault("activity_segments", [])
    if not isinstance(recent, list):
        recent = []
        state["activity_segments"] = recent
    recent.append(segment)
    if len(recent) > RECENT_SEGMENT_LIMIT:
        del recent[:-RECENT_SEGMENT_LIMIT]


def end_activity_segment(state: dict[str, Any], at: str) -> bool:
    """Close the current segment once. A repeated end is an idempotent no-op."""
    current = state.get("activity_current")
    if not isinstance(current, dict):
        state["activity_current"] = None
        return False
    _reject_state_clock_rollback(state, at)
    seconds = _elapsed(current.get("started_at"), at)
    if seconds is None:
        raise ActivityTimingError("activity end is before start or has an invalid timestamp")
    kind = str(current.get("kind") or "")
    reason = str(current.get("reason") or "")
    validate_activity(kind, reason)
    phase = str(current.get("phase") or "unknown")
    segment = {
        "kind": kind,
        "phase": phase,
        "reason": reason,
        "started_at": current["started_at"],
        "ended_at": at,
        "duration_sec": seconds,
    }
    detail = sanitize_activity_detail(current.get("detail"))
    if detail:
        segment["detail"] = detail
    _record_closed_segment(state, segment)
    state["activity_current"] = None
    return True


def _resume_boundary(state: dict[str, Any], at: str) -> str:
    current = state.get("activity_current")
    started_at = current.get("started_at") if isinstance(current, dict) else None
    started = _parse_at(started_at)
    resumed = _parse_at(at)
    updated_text = state.get("updated_at")
    updated = _parse_at(updated_text)
    if not started or not resumed:
        raise ActivityTimingError("invalid resume timestamp")
    if resumed < started:
        raise ActivityTimingError("resume timestamp is before activity start")
    if updated and resumed < updated:
        raise ActivityTimingError("resume timestamp is before the last state update")
    boundary = updated if updated and started <= updated <= resumed else started
    gap = (resumed - boundary).total_seconds()
    if gap > 0:
        previous = _finite_nonnegative(state.get("activity_unobserved_gap_sec")) or 0.0
        state["activity_unobserved_gap_sec"] = previous + gap
    return boundary.strftime("%Y-%m-%dT%H:%M:%SZ")


def close_activity_for_resume(state: dict[str, Any], at: str) -> bool:
    current = state.get("activity_current")
    if current is None:
        state["activity_current"] = None
        return False
    if not isinstance(current, dict):
        raise ActivityTimingError("activity current is malformed")
    return end_activity_segment(state, _resume_boundary(state, at))


def close_activity_for_terminal(state: dict[str, Any], at: str) -> bool:
    """Close valid measurement, but never let corrupt observability block control."""
    current = state.get("activity_current")
    if current is None:
        state["activity_current"] = None
        return False
    if not isinstance(current, dict):
        state["activity_current"] = None
        _record_anomaly(state, "invalid-current-terminal")
        return True
    try:
        kind = current.get("kind")
        reason = current.get("reason")
        if not isinstance(kind, str) or not isinstance(reason, str):
            raise ActivityTimingError("activity current labels are malformed")
        validate_activity(kind, reason)
        return end_activity_segment(state, _resume_boundary(state, at))
    except ActivityTimingError:
        state["activity_current"] = None
        _record_anomaly(state, "invalid-current-terminal")
        return True


def start_activity_segment(
    state: dict[str, Any],
    kind: str,
    reason: str,
    at: str,
    *,
    detail: str | None = None,
    resume: bool = False,
) -> bool:
    validate_activity(kind, reason)
    _reject_state_clock_rollback(state, at)
    if state.get("phase") in TERMINAL_PHASES or state.get("loop_active") is False:
        raise ActivityTimingError("cannot start activity in a terminal state")
    current = state.get("activity_current")
    clean_detail = sanitize_activity_detail(detail)
    if isinstance(current, dict):
        same = (
            current.get("kind") == kind
            and current.get("reason") == reason
            and current.get("phase") == (state.get("phase") or "unknown")
            and sanitize_activity_detail(current.get("detail")) == clean_detail
        )
        # A normal duplicate start is idempotent.  On resume, however, equal
        # labels can still describe a stale pre-crash segment.  Close that
        # segment once unless this exact resume timestamp is already current.
        if same and (not resume or current.get("started_at") == at):
            return False
        end_activity_segment(state, _resume_boundary(state, at) if resume else at)
    entry = {
        "kind": kind,
        "phase": state.get("phase") or "unknown",
        "reason": reason,
        "started_at": at,
    }
    if clean_detail:
        entry["detail"] = clean_detail
    state["activity_current"] = entry
    state.setdefault("activity_segments", [])
    _rollup(state)
    return True


def transition_activity_phase(state: dict[str, Any], new_phase: str, at: str) -> None:
    current = state.get("activity_current")
    if new_phase in TERMINAL_PHASES:
        close_activity_for_terminal(state, at)
        return
    if not isinstance(current, dict) or current.get("phase") == new_phase:
        return
    preserved = dict(current)
    end_activity_segment(state, at)
    if new_phase in TERMINAL_PHASES:
        return
    state["activity_current"] = {
        key: value
        for key, value in {
            "kind": preserved.get("kind"),
            "phase": new_phase,
            "reason": preserved.get("reason"),
            "started_at": at,
            "detail": sanitize_activity_detail(preserved.get("detail")),
        }.items()
        if value is not None
    }


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * probability
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(ordered[lower])
    weight = rank - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _percentiles(samples: list[float]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "p50": _percentile(samples, 0.5),
        "p90": _percentile(samples, 0.9),
    }


def _valid_phase_durations(state: dict[str, Any]) -> float:
    raw = state.get("phase_durations_sec")
    if not isinstance(raw, dict):
        return 0.0
    closed = sum(
        value
        for candidate in raw.values()
        if (value := _finite_nonnegative(candidate)) is not None
    )
    if state.get("phase") in TERMINAL_PHASES:
        return closed
    current = _elapsed(state.get("phase_started_at"), state.get("updated_at"))
    return closed + (current or 0.0)


def _valid_open_activity(state: dict[str, Any]) -> bool:
    current = state.get("activity_current")
    if not isinstance(current, dict):
        return False
    kind = current.get("kind")
    reason = current.get("reason")
    if not isinstance(kind, str) or kind not in ACTIVITY_KINDS:
        return False
    if not isinstance(reason, str) or reason not in ACTIVITY_REASONS_BY_KIND[kind]:
        return False
    if not isinstance(current.get("phase"), str):
        return False
    started = _parse_at(current.get("started_at"))
    updated = _parse_at(state.get("updated_at"))
    return bool(started and (not updated or started <= updated))


def _state_activity(state: dict[str, Any]) -> dict[str, Any]:
    kinds: dict[str, float] = {}
    phases: dict[str, dict[str, float]] = {}
    reasons: dict[str, dict[str, float]] = {}
    invalid = 0
    totals_consistent = True
    closed_count = 0
    rollup = state.get("activity_rollup")
    if isinstance(rollup, dict) and "closed_segment_count" in rollup:
        raw_kinds = rollup.get("activity_duration_totals_sec")
        if isinstance(raw_kinds, dict):
            for kind, raw in raw_kinds.items():
                seconds = _finite_nonnegative(raw)
                if kind not in ACTIVITY_KINDS or seconds is None:
                    invalid += 1
                    continue
                kinds[kind] = seconds
        raw_phases = rollup.get("phase_activity_duration_totals_sec")
        if isinstance(raw_phases, dict):
            for phase, raw_phase in raw_phases.items():
                if not isinstance(phase, str) or not isinstance(raw_phase, dict):
                    invalid += 1
                    continue
                for kind, raw in raw_phase.items():
                    seconds = _finite_nonnegative(raw)
                    if kind not in ACTIVITY_KINDS or seconds is None:
                        invalid += 1
                        continue
                    phases.setdefault(phase, {})[kind] = seconds
        raw_reasons = rollup.get("wait_reason_totals_sec")
        if isinstance(raw_reasons, dict):
            for kind, raw_reason in raw_reasons.items():
                if kind not in WAIT_KINDS or not isinstance(raw_reason, dict):
                    invalid += 1
                    continue
                for reason, raw in raw_reason.items():
                    seconds = _finite_nonnegative(raw)
                    normalized_reason = str(reason or "unknown")
                    if (
                        seconds is None
                        or normalized_reason not in ACTIVITY_REASONS_BY_KIND[kind]
                        and normalized_reason != "unknown"
                    ):
                        invalid += 1
                        continue
                    reasons.setdefault(kind, {})[normalized_reason] = seconds
        raw_closed_count = rollup.get("closed_segment_count")
        if isinstance(raw_closed_count, bool) or not isinstance(raw_closed_count, int) or raw_closed_count < 0:
            invalid += 1
            closed_count = 0
        else:
            closed_count = raw_closed_count
        observed_rollup = _finite_nonnegative(rollup.get("observed_total_sec"))
        kind_sum = sum(kinds.values())
        phase_sum = sum(sum(by_kind.values()) for by_kind in phases.values())
        for candidate in (observed_rollup,):
            if candidate is None or abs(candidate - kind_sum) > 0.001:
                invalid += 1
                totals_consistent = False
        if abs(phase_sum - kind_sum) > 0.001:
            invalid += 1
            totals_consistent = False
        for kind in WAIT_KINDS:
            reason_sum = sum(reasons.get(kind, {}).values())
            kind_total = kinds.get(kind, 0.0)
            if abs(reason_sum - kind_total) > 0.001:
                invalid += 1
                totals_consistent = False
    else:
        segments = state.get("activity_segments")
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict) or "ended_at" not in segment:
                    continue
                kind = segment.get("kind")
                phase = segment.get("phase")
                seconds = _finite_nonnegative(segment.get("duration_sec"))
                calculated = _elapsed(segment.get("started_at"), segment.get("ended_at"))
                if (
                    not isinstance(kind, str)
                    or kind not in ACTIVITY_KINDS
                    or not isinstance(phase, str)
                    or seconds is None
                    or calculated is None
                    or abs(seconds - calculated) > 0.001
                ):
                    invalid += 1
                    continue
                raw_reason = segment.get("reason")
                reason = str(raw_reason or "unknown")
                if reason not in ACTIVITY_REASONS_BY_KIND[kind] and reason != "unknown":
                    invalid += 1
                    continue
                closed_count += 1
                kinds[kind] = kinds.get(kind, 0.0) + seconds
                phase_row = phases.setdefault(phase, {})
                phase_row[kind] = phase_row.get(kind, 0.0) + seconds
                if kind in WAIT_KINDS:
                    reason_row = reasons.setdefault(kind, {})
                    reason_row[reason] = reason_row.get(reason, 0.0) + seconds
    observed = sum(kinds.values())
    raw_current = state.get("activity_current")
    open_count = 1 if _valid_open_activity(state) else 0
    if raw_current is not None and not open_count:
        invalid += 1
    anomaly_counts = state.get("activity_anomaly_counts")
    if isinstance(anomaly_counts, dict):
        for count in anomaly_counts.values():
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                invalid += count
            else:
                invalid += 1
    raw_gap = state.get("activity_unobserved_gap_sec")
    valid_gap = _finite_nonnegative(raw_gap)
    if raw_gap is not None and valid_gap is None:
        invalid += 1
    return {
        "observed": observed,
        "kinds": kinds,
        "phases": phases,
        "reasons": reasons,
        "invalid": invalid,
        "open": open_count,
        "closed": closed_count,
        "valid_closed_sample": bool(closed_count > 0 and kinds and phases),
        "phase_wall": _valid_phase_durations(state),
        "unobserved_gap": valid_gap or 0.0,
        "totals_consistent": totals_consistent,
    }


def summarize_activity_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    kind_totals: dict[str, float] = {}
    reason_totals: dict[str, dict[str, float]] = {}
    task_samples: dict[str, list[float]] = {}
    phase_samples: dict[str, list[float]] = {}
    observed_total = 0.0
    phase_wall_total = 0.0
    invalid_count = 0
    open_count = 0
    closed_count = 0
    unobserved_gap = 0.0
    states_with = 0
    totals_consistent = True
    for state in states:
        item = _state_activity(state)
        if item["observed"] or item["open"] or item["closed"]:
            states_with += 1
        observed_total += item["observed"]
        phase_wall_total += item["phase_wall"]
        invalid_count += item["invalid"]
        open_count += item["open"]
        closed_count += item["closed"]
        unobserved_gap += item["unobserved_gap"]
        totals_consistent = totals_consistent and item["totals_consistent"]
        task_key = str(state.get("mission_id") or "unknown")
        if item["valid_closed_sample"]:
            task_samples.setdefault(task_key, []).append(item["observed"])
        for kind, seconds in item["kinds"].items():
            kind_totals[kind] = kind_totals.get(kind, 0.0) + seconds
        for phase, by_kind in item["phases"].items():
            phase_total = sum(by_kind.values())
            if by_kind:
                phase_samples.setdefault(phase, []).append(phase_total)
        for kind, by_reason in item["reasons"].items():
            for reason, seconds in by_reason.items():
                row = reason_totals.setdefault(kind, {})
                row[reason] = row.get(reason, 0.0) + seconds
    unclassified = max(0.0, phase_wall_total - observed_total)
    coverage_denominator = max(phase_wall_total, observed_total)
    coverage_ratio = observed_total / coverage_denominator if coverage_denominator > 0 else None
    if observed_total > phase_wall_total + 0.001:
        totals_consistent = False
    if invalid_count > 0:
        totals_consistent = False
    return {
        "percentile_method": PERCENTILE_METHOD,
        "task_key": TASK_KEY_METHOD,
        "task_duration_percentiles_sec": {
            key: _percentiles(values) for key, values in sorted(task_samples.items())
        },
        "phase_duration_percentiles_sec": {
            key: _percentiles(values) for key, values in sorted(phase_samples.items())
        },
        "activity_duration_totals_sec": dict(sorted(kind_totals.items())),
        "wait_reason_totals_sec": {
            kind: dict(sorted(reasons.items()))
            for kind, reasons in sorted(reason_totals.items())
        },
        "observed_total_sec": observed_total,
        "phase_duration_total_sec": phase_wall_total,
        "coverage_denominator_sec": coverage_denominator,
        "unclassified_sec": unclassified,
        "coverage_ratio": coverage_ratio,
        "unobserved_gap_sec": unobserved_gap,
        "closed_segment_count": closed_count,
        "open_segment_count": open_count,
        "invalid_segment_count": invalid_count,
        "states_with_activity_count": states_with,
        "states_without_activity_count": len(states) - states_with,
        "totals_consistent": totals_consistent,
    }
