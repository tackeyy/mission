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
        {"human_quality_score": 4.3, "elapsed_minutes": 1.0},
        {"human_quality_score": 4.3, "elapsed_minutes": 5.0},
        {"human_quality_score": 4.3, "elapsed_minutes": 9.0},
        {"human_quality_score": 4.3, "elapsed_minutes": 100.0, "run_status": "blocked"},
    ])

    assert summary["duration_minutes"] == {
        "included_records": 3,
        "blocked_censored_records": 1,
        "invalid_records": 0,
        "p50": 5.0,
        "p90": 8.2,
        "tail": 9.0,
    }
    assert summary["planning_provider_kpi"] == {
        "status": "deferred",
        "schema": "mission-planning-provider-kpi/1",
    }
    assert summary["measurement_observations"] == {
        key: {"status": "unavailable", "value": None}
        for key in (
            "artifact_observation_coverage",
            "activity_coverage",
            "structured_score_provenance",
            "reviewer_freshness",
            "force_pass_rate",
            "expected_gate_retry_count",
            "group_closeout_completeness",
        )
    }


def test_provider_kpi_payload_is_rejected_until_the_versioned_consumer_is_enabled():
    audit = _load()

    with pytest.raises(audit.BenchmarkAuditInputError, match="deferred"):
        audit.summarize_benchmark_kpi([{
            "human_quality_score": 4.3,
            "planning_provider_kpi": {"schema": "mission-planning-provider-kpi/1"},
        }])


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
