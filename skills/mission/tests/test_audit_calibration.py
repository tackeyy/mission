"""Issue #390: synthetic benchmark audit calibration contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    path = BENCH / "benchmark_audit.py"
    spec = importlib.util.spec_from_file_location("benchmark_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner():
    path = BENCH / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kpi_buckets_dedupes_defects_and_keeps_retry_separate():
    audit = _load()
    summary = audit.summarize_benchmark_kpi([
        {
            "human_quality_score": 3.9,
            "elapsed_minutes": 3.0,
            "audit_events": [
                {"root_event_id": "root-a", "attempt": 1, "kind": "defect"},
                {"root_event_id": "root-a", "attempt": 2, "retry_of": "attempt-a", "kind": "defect"},
                {"root_event_id": "root-gate", "attempt": 1, "kind": "expected-gate"},
            ],
            "audit_context": {"coverage": {"observed": 2, "eligible": 3}, "tier": "standard"},
        },
        {
            "human_quality_score": 4.1,
            "elapsed_minutes": 5.0,
            "audit_events": [{"root_event_id": "root-b", "attempt": 1, "kind": "defect"}],
            "audit_context": {"coverage": {"observed": 1, "eligible": 1}, "tier": "full"},
        },
        {"human_quality_score": 4.3, "elapsed_minutes": 7.0, "run_status": "blocked"},
    ])

    assert summary["score_buckets"] == {
        "below_pass": 1,
        "pass_but_below_target": 1,
        "target_met": 1,
        "invalid": 0,
    }
    assert summary["defects"] == {
        "unique_root_events": 2,
        "event_attempts": 3,
        "retry_attempts": 1,
        "expected_gate_events": 1,
    }
    assert summary["coverage"] == {"observed": 3, "eligible": 4, "ratio": 0.75}
    assert summary["tier_context"] == {"standard": 1, "full": 1, "unclassified": 1}


def test_duration_percentiles_censor_blocked_records_and_defer_unavailable_provider_kpi():
    audit = _load()
    summary = audit.summarize_benchmark_kpi([
        {"human_quality_score": 4.3, "elapsed_minutes": 1.0, "run_status": "completed"},
        {"human_quality_score": 4.3, "elapsed_minutes": 5.0, "run_status": "completed"},
        {"human_quality_score": 4.3, "elapsed_minutes": 9.0, "run_status": "completed"},
        {"human_quality_score": 4.3, "elapsed_minutes": 100.0, "run_status": "blocked"},
    ])

    assert summary["duration_minutes"] == {
        "included_records": 3,
        "blocked_censored_records": 1,
        "noncompleted_excluded_records": 0,
        "invalid_records": 0,
        "p50": 5.0,
        "p90": 8.2,
        "tail": 9.0,
    }
    assert summary["planning_provider_kpi"] == {
        "status": "deferred",
        "schema": "mission-planning-provider-kpi/1",
    }
    for key, observation in summary["measurement_observations"].items():
        assert observation["status"] == "unavailable", key
        assert observation.get("rate", observation.get("count", observation.get("value"))) is None, key


def test_benchmark_consumer_accepts_only_valid_versioned_planning_provider_block():
    audit = _load()
    from planning_provider_metrics import reduce_planning_provider_kpis
    block = reduce_planning_provider_kpis([], population_kind="controlled")
    assert audit.summarize_benchmark_kpi([{"planning_provider_kpi": block}])["planning_provider_kpi"]["status"] == "observed"
    block["schema"] = "mission-planning-provider-kpi/9"
    with pytest.raises(audit.BenchmarkAuditInputError):
        audit.summarize_benchmark_kpi([{"planning_provider_kpi": block}])


def test_duration_percentiles_exclude_failed_and_other_noncompleted_records():
    audit = _load()
    summary = audit.summarize_benchmark_kpi([
        {"human_quality_score": 4.3, "elapsed_minutes": 2.0, "run_status": "completed"},
        {"human_quality_score": 4.3, "elapsed_minutes": 98.0, "run_status": "failed"},
        {"human_quality_score": 4.3, "elapsed_minutes": 200.0, "run_status": "blocked"},
    ])

    assert summary["duration_minutes"] == {
        "included_records": 1,
        "blocked_censored_records": 1,
        "noncompleted_excluded_records": 1,
        "invalid_records": 0,
        "p50": 2.0,
        "p90": 2.0,
        "tail": 2.0,
    }


def test_provider_kpi_payload_is_rejected_until_the_versioned_consumer_is_enabled():
    audit = _load()

    with pytest.raises(audit.BenchmarkAuditInputError, match="deferred"):
        audit.summarize_benchmark_kpi([{
            "human_quality_score": 4.3,
            "planning_provider_kpi": {"schema": "mission-planning-provider-kpi/1"},
        }])


def _valid_measurement_observations():
    return {
        "artifact_observation_coverage": {"status": "observed", "numerator": 1, "denominator": 1},
        "activity_coverage": {"status": "observed", "numerator": 1, "denominator": 1},
        "structured_score_provenance": {"status": "observed", "numerator": 1, "denominator": 1},
        "reviewer_freshness": {"status": "observed", "numerator": 1, "denominator": 1},
        "force_pass_rate": {"status": "observed", "numerator": 0, "denominator": 1},
        "expected_gate_retry_count": {"status": "observed", "count": 0},
        "group_closeout_completeness": {"status": "unavailable", "value": None},
    }


@pytest.mark.parametrize(
    ("field", "observation"),
    [
        ("artifact_observation_coverage", {"status": "observed", "numerator": True, "denominator": 1}),
        ("activity_coverage", {"status": "observed", "numerator": float("nan"), "denominator": 1}),
        ("structured_score_provenance", {"status": "observed", "numerator": -1, "denominator": 1}),
        ("reviewer_freshness", {"status": "observed", "numerator": 2, "denominator": 1}),
        ("force_pass_rate", {"status": "observed", "numerator": 0}),
        ("expected_gate_retry_count", {"status": "observed"}),
        ("expected_gate_retry_count", {"status": "observed", "count": True}),
        ("expected_gate_retry_count", {"status": "observed", "count": -1}),
        ("group_closeout_completeness", {"status": "observed"}),
        ("group_closeout_completeness", {"status": "observed", "value": None}),
        ("group_closeout_completeness", {"status": "observed", "value": True}),
        ("group_closeout_completeness", {"status": "observed", "value": float("nan")}),
        ("group_closeout_completeness", {"status": "observed", "value": -0.1}),
        ("group_closeout_completeness", {"status": "observed", "value": 1.1}),
        ("group_closeout_completeness", {"status": "unavailable", "value": 0.0}),
        ("group_closeout_completeness", {"status": "not-applicable", "value": 0.0}),
        ("group_closeout_completeness", {"status": "unknown", "value": None}),
        ("group_closeout_completeness", []),
        ("artifact_observation_coverage", {"status": "unknown", "value": None}),
        ("activity_coverage", []),
    ],
)
def test_present_malformed_measurement_observations_fail_closed(field, observation):
    audit = _load()
    measurements = _valid_measurement_observations()
    measurements[field] = observation

    with pytest.raises(audit.BenchmarkAuditInputError, match="measurement observation"):
        audit.summarize_benchmark_kpi([{
            "human_quality_score": 4.3,
            "measurement_observations": measurements,
        }])


def test_missing_measurement_document_or_field_stays_unavailable():
    audit = _load()
    measurements = _valid_measurement_observations()
    del measurements["activity_coverage"]
    summary = audit.summarize_benchmark_kpi([
        {"human_quality_score": 4.3},
        {"human_quality_score": 4.3, "measurement_observations": measurements},
    ])

    assert summary["measurement_observations"]["activity_coverage"]["status"] == "unavailable"
    assert summary["measurement_observations"]["activity_coverage"]["unavailable_records"] == 2


def test_observed_zero_denominator_rate_is_explicitly_null():
    audit = _load()
    measurements = _valid_measurement_observations()
    measurements["activity_coverage"] = {
        "status": "observed", "numerator": 0, "denominator": 0,
    }
    summary = audit.summarize_benchmark_kpi([{
        "human_quality_score": 4.3,
        "measurement_observations": measurements,
    }])

    assert summary["measurement_observations"]["activity_coverage"]["rate"] is None


def test_observed_group_closeout_keeps_its_typed_value():
    audit = _load()
    measurements = _valid_measurement_observations()
    measurements["group_closeout_completeness"] = {"status": "observed", "value": 0.5}
    summary = audit.summarize_benchmark_kpi([{
        "human_quality_score": 4.3,
        "measurement_observations": measurements,
    }])

    assert summary["measurement_observations"]["group_closeout_completeness"] == {
        "status": "observed", "value": 0.5, "observed_records": 1,
        "unavailable_records": 0, "not_applicable_records": 0,
    }


def test_kpi_aggregates_versioned_mission_observations_without_reading_state():
    audit = _load()
    observed = {
        "artifact_observation_coverage": {"status": "observed", "numerator": 1, "denominator": 1},
        "activity_coverage": {"status": "observed", "numerator": 30.0, "denominator": 60.0},
        "structured_score_provenance": {"status": "observed", "numerator": 1, "denominator": 2},
        "reviewer_freshness": {"status": "observed", "numerator": 2, "denominator": 2},
        "force_pass_rate": {"status": "observed", "numerator": 0, "denominator": 1},
        "expected_gate_retry_count": {"status": "observed", "count": 2},
        "group_closeout_completeness": {"status": "unavailable", "value": None},
    }
    not_applicable = {
        key: {"status": "not-applicable", "value": None}
        for key in observed
    }
    summary = audit.summarize_benchmark_kpi([
        {"arm": "mission", "human_quality_score": 4.3, "measurement_observations": observed},
        {"arm": "claude_code_goal_command", "human_quality_score": 4.3, "measurement_observations": not_applicable},
    ])

    artifact = summary["measurement_observations"]["artifact_observation_coverage"]
    assert artifact["numerator"] == 1
    assert artifact["denominator"] == 1
    assert artifact["rate"] == 1.0
    assert artifact["not_applicable_records"] == 1
    activity = summary["measurement_observations"]["activity_coverage"]
    assert activity["rate"] == 0.5
    assert summary["measurement_observations"]["expected_gate_retry_count"] == {
        "status": "observed", "count": 2, "observed_records": 1,
        "unavailable_records": 0, "not_applicable_records": 1,
    }
    assert summary["measurement_observations"]["group_closeout_completeness"] == {
        "status": "unavailable", "value": None, "observed_records": 0,
        "unavailable_records": 1, "not_applicable_records": 1,
    }


def test_runner_summary_publishes_benchmark_kpi_with_blocked_duration_censoring():
    runner = _runner()
    records = [
        {
            "arm": "mission", "run_status": "completed", "comparable_attempt": True,
            "completion": True, "validator_pass": True, "human_quality_score": 4.3,
            "intervention_count": 0, "evidence_completeness": 4.0,
            "quality_marker_score": 0.5, "elapsed_minutes": 2.0, "total_cost_usd": 1.0,
        },
        {
            "arm": "mission", "run_status": "blocked", "comparable_attempt": False,
            "completion": False, "validator_pass": False, "human_quality_score": 3.0,
            "intervention_count": 0, "evidence_completeness": 1.0,
            "quality_marker_score": None, "elapsed_minutes": 80.0, "total_cost_usd": 1.0,
        },
    ]
    summary = runner.summarize(records, [{"id": "synthetic"}], "rid", "abc", BENCH / "tasks.json")

    assert summary["benchmark_kpi"]["duration_minutes"]["included_records"] == 1
    assert summary["benchmark_kpi"]["duration_minutes"]["blocked_censored_records"] == 1
