import sys
import json
import subprocess
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from planning_provider_metrics import PlanningProviderMetricError, reduce_planning_provider_kpis, validate_planning_provider_kpis


def test_zero_denominator_rate_is_null_and_required_totals_exist():
    result = reduce_planning_provider_kpis([], population_kind="controlled")
    totals = result["totals"]
    assert result["schema"] == "mission-planning-provider-kpi/1"
    assert totals["eligible_complex_planning_selection"] == {"numerator": 0, "denominator": 0, "rate": None}
    assert totals["preflight_live_digest_match"]["rate"] is None
    assert totals["canonical_plan_executor_lineage"]["rate"] is None


def test_reducer_rejects_unknown_population_kind():
    with pytest.raises(PlanningProviderMetricError, match="population"):
        reduce_planning_provider_kpis([], population_kind="unknown")


def test_policy_v1_lineage_and_rejected_drift_are_counted_without_lowering_live_rate():
    state = {
        "planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "provider-primary",
        "task_profile": {"primary": "general"},
        "specialist_invocations": [
            {"invocation_id": "inv_ok", "phase": "planning", "status": "completed"},
            {"invocation_id": "inv_rejected", "phase": "planning", "status": "failed", "reason_code": "preflight-required"},
        ],
        "provider_preflights": {"p": {"invocation_id": "inv_ok", "status": "consumed"}},
        "canonical_plan": {"digest": "sha256:plan", "generation": 1},
        "executor_handoff": {"handoff_id": "h"},
        "decisions": [{"step_id": "s", "handoff_id": "h", "plan_digest": "sha256:plan", "plan_generation": 1}],
    }
    totals = reduce_planning_provider_kpis([state], population_kind="controlled")["totals"]
    assert totals["eligible_complex_planning_selection"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert totals["preflight_live_digest_match"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert totals["canonical_plan_executor_lineage"] == {"numerator": 1, "denominator": 1, "rate": 1.0}


def test_core_strategy_is_eligible_but_not_provider_selection_and_counters_are_fail_closed():
    state = {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "core",
             "ineligible_external_planning_invocations": 1, "dry_run_external_effect_count": 0,
             "authority_injection_accept_count": 0, "legacy_session_retroactive_provider_invocations": 0}
    result = reduce_planning_provider_kpis([state], population_kind="controlled")
    assert result["totals"]["eligible_complex_planning_selection"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    state["dry_run_external_effect_count"] = True
    with pytest.raises(PlanningProviderMetricError, match="counter"):
        reduce_planning_provider_kpis([state], population_kind="controlled")


@pytest.mark.parametrize("mutate", [
    lambda value: value["totals"]["eligible_complex_planning_selection"].update({"numerator": 2, "denominator": 1}),
    lambda value: value["totals"]["eligible_complex_planning_selection"].update({"numerator": False}),
    lambda value: value["totals"].pop("authority_injection_accept_count"),
    lambda value: value.update({"schema": "mission-planning-provider-kpi/9"}),
])
def test_versioned_kpi_contract_rejects_invalid_totals(mutate):
    value = reduce_planning_provider_kpis([], population_kind="controlled")
    mutate(value)
    with pytest.raises(PlanningProviderMetricError):
        validate_planning_provider_kpis(value)


def test_stats_and_audit_emit_identical_kpi_for_same_state_snapshot(run_cli, tmp_path):
    run_cli("init", "metric parity", "--complexity", "Complex", cwd=tmp_path, check=True)
    stats = json.loads(run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True).stdout)
    audit_cli = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"
    audit = subprocess.run([sys.executable, str(audit_cli), "--root", str(tmp_path), "--json"], capture_output=True, text=True, check=True)
    assert json.loads(audit.stdout)["planning_provider_kpis"] == stats["planning_provider_kpis"]
