"""Synthetic-only KPI reduction for benchmark result records.

The reducer receives benchmark records already produced by a runner.  It never
opens mission state, so it cannot recompute or reinterpret planning state.
Optional ``audit_events`` and ``audit_context`` are benchmark annotations:

* events require a root event id and attempt; expected gates are reported but
  excluded from defect totals;
* coverage is an observed/eligible pair and tier is presentation context;
* blocked records are censored from duration percentiles but remain counted.

``mission-planning-provider-kpi/1`` is intentionally not consumed yet.  The
producer is delivered by Issue #399; accepting an unvalidated payload here
would make this consumer silently drift from that versioned contract.
"""

from __future__ import annotations

import math


MEASUREMENT_OBSERVATION_FIELDS = (
    "artifact_observation_coverage",
    "activity_coverage",
    "structured_score_provenance",
    "reviewer_freshness",
    "force_pass_rate",
    "expected_gate_retry_count",
    "group_closeout_completeness",
)
MEASUREMENT_RATE_FIELDS = MEASUREMENT_OBSERVATION_FIELDS[:5]
MEASUREMENT_COUNTER_FIELDS = ("expected_gate_retry_count",)



class BenchmarkAuditInputError(ValueError):
    """Raised when a benchmark audit annotation is not safe to aggregate."""


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _percentile_r7(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = 1 + (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower - 1]
    fraction = position - lower
    return ordered[lower - 1] + (ordered[upper - 1] - ordered[lower - 1]) * fraction


def _score_bucket(value: object) -> str:
    score = _finite_number(value)
    if score is None or score < 1.0 or score > 5.0:
        return "invalid"
    if score < 4.0:
        return "below_pass"
    if score < 4.3:
        return "pass_but_below_target"
    return "target_met"


def _validate_event(value: object) -> dict:
    if not isinstance(value, dict):
        raise BenchmarkAuditInputError("audit event must be an object")
    root_event_id = value.get("root_event_id")
    attempt = value.get("attempt")
    kind = value.get("kind")
    retry_of = value.get("retry_of")
    if not isinstance(root_event_id, str) or not root_event_id:
        raise BenchmarkAuditInputError("audit event root_event_id is required")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise BenchmarkAuditInputError("audit event attempt must be a positive integer")
    if kind not in {"defect", "expected-gate"}:
        raise BenchmarkAuditInputError("audit event kind is invalid")
    if retry_of is not None and (not isinstance(retry_of, str) or not retry_of):
        raise BenchmarkAuditInputError("audit event retry_of is invalid")
    return value


def _context(record: dict) -> tuple[str, int, int]:
    context = record.get("audit_context")
    if context is None:
        return "unclassified", 0, 0
    if not isinstance(context, dict):
        raise BenchmarkAuditInputError("audit_context must be an object")
    tier = context.get("tier", "unclassified")
    if not isinstance(tier, str) or not tier:
        raise BenchmarkAuditInputError("audit_context tier is invalid")
    coverage = context.get("coverage")
    if coverage is None:
        return tier, 0, 0
    if not isinstance(coverage, dict):
        raise BenchmarkAuditInputError("coverage must be an object")
    observed = coverage.get("observed")
    eligible = coverage.get("eligible")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (observed, eligible)):
        raise BenchmarkAuditInputError("coverage counts must be non-negative integers")
    if observed > eligible:
        raise BenchmarkAuditInputError("coverage observed cannot exceed eligible")
    return tier, observed, eligible


def _measurement_observations(records: list[dict]) -> dict:
    """Aggregate only runner-supplied observations; never reopen mission state."""
    output: dict[str, dict] = {}
    for field in MEASUREMENT_RATE_FIELDS:
        numerator = denominator = 0.0
        observed = unavailable = not_applicable = 0
        for record in records:
            document = record.get("measurement_observations")
            value = document.get(field) if isinstance(document, dict) else None
            status = value.get("status") if isinstance(value, dict) else "unavailable"
            if status == "not-applicable":
                not_applicable += 1
                continue
            if status != "observed":
                unavailable += 1
                continue
            raw_numerator = _finite_number(value.get("numerator"))
            raw_denominator = _finite_number(value.get("denominator"))
            if raw_numerator is None or raw_denominator is None or raw_numerator < 0 or raw_denominator < 0 or raw_numerator > raw_denominator:
                unavailable += 1
                continue
            numerator += raw_numerator
            denominator += raw_denominator
            observed += 1
        output[field] = {
            "status": "observed" if observed else "unavailable",
            "numerator": int(numerator) if numerator.is_integer() else numerator,
            "denominator": int(denominator) if denominator.is_integer() else denominator,
            "rate": round(numerator / denominator, 4) if denominator else None,
            "observed_records": observed,
            "unavailable_records": unavailable,
            "not_applicable_records": not_applicable,
        }
    for field in MEASUREMENT_COUNTER_FIELDS:
        count = observed = unavailable = not_applicable = 0
        for record in records:
            document = record.get("measurement_observations")
            value = document.get(field) if isinstance(document, dict) else None
            status = value.get("status") if isinstance(value, dict) else "unavailable"
            if status == "not-applicable":
                not_applicable += 1
                continue
            if status != "observed":
                unavailable += 1
                continue
            raw_count = value.get("count")
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                unavailable += 1
                continue
            count += raw_count
            observed += 1
        output[field] = {
            "status": "observed" if observed else "unavailable",
            "count": count if observed else None,
            "observed_records": observed,
            "unavailable_records": unavailable,
            "not_applicable_records": not_applicable,
        }
    for field in set(MEASUREMENT_OBSERVATION_FIELDS) - set(output):
        observed = unavailable = not_applicable = 0
        for record in records:
            document = record.get("measurement_observations")
            value = document.get(field) if isinstance(document, dict) else None
            status = value.get("status") if isinstance(value, dict) else "unavailable"
            if status == "not-applicable":
                not_applicable += 1
            elif status == "observed":
                observed += 1
            else:
                unavailable += 1
        output[field] = {
            "status": "observed" if observed else "unavailable",
            "value": None,
            "observed_records": observed,
            "unavailable_records": unavailable,
            "not_applicable_records": not_applicable,
        }
    return {field: output[field] for field in MEASUREMENT_OBSERVATION_FIELDS}


def summarize_benchmark_kpi(records: list[dict]) -> dict:
    """Return a JSON-safe benchmark KPI summary from synthetic runner records."""
    score_buckets = {
        "below_pass": 0,
        "pass_but_below_target": 0,
        "target_met": 0,
        "invalid": 0,
    }
    defects: set[str] = set()
    event_attempts = retry_attempts = expected_gate_events = 0
    tier_context: dict[str, int] = {}
    coverage_observed = coverage_eligible = 0
    durations: list[float] = []
    blocked_censored = invalid_durations = 0

    for record in records:
        if not isinstance(record, dict):
            raise BenchmarkAuditInputError("benchmark record must be an object")
        if "planning_provider_kpi" in record:
            raise BenchmarkAuditInputError("planning provider KPI consumer is deferred pending Issue #399")

        score_buckets[_score_bucket(record.get("human_quality_score"))] += 1
        tier, observed, eligible = _context(record)
        tier_context[tier] = tier_context.get(tier, 0) + 1
        coverage_observed += observed
        coverage_eligible += eligible

        events = record.get("audit_events", [])
        if not isinstance(events, list):
            raise BenchmarkAuditInputError("audit_events must be a list")
        for raw_event in events:
            event = _validate_event(raw_event)
            if event["kind"] == "expected-gate":
                expected_gate_events += 1
                continue
            event_attempts += 1
            defects.add(event["root_event_id"])
            if event.get("retry_of") is not None:
                retry_attempts += 1

        if record.get("run_status") == "blocked":
            blocked_censored += 1
            continue
        duration = _finite_number(record.get("elapsed_minutes"))
        if duration is None or duration < 0:
            invalid_durations += 1
            continue
        durations.append(duration)

    return {
        "schema": "mission-benchmark-kpi/1",
        "score_buckets": score_buckets,
        "defects": {
            "unique_root_events": len(defects),
            "event_attempts": event_attempts,
            "retry_attempts": retry_attempts,
            "expected_gate_events": expected_gate_events,
        },
        "coverage": {
            "observed": coverage_observed,
            "eligible": coverage_eligible,
            "ratio": round(coverage_observed / coverage_eligible, 4) if coverage_eligible else None,
        },
        "tier_context": tier_context,
        "duration_minutes": {
            "included_records": len(durations),
            "blocked_censored_records": blocked_censored,
            "invalid_records": invalid_durations,
            "p50": _percentile_r7(durations, 0.5),
            "p90": _percentile_r7(durations, 0.9),
            "tail": max(durations) if durations else None,
        },
        "planning_provider_kpi": {
            "status": "deferred",
            "schema": "mission-planning-provider-kpi/1",
        },
        "measurement_observations": _measurement_observations(records),
    }
