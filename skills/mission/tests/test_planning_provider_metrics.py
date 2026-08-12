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
            {"invocation_id": "inv_ok", "phase": "planning", "status": "completed", "input_outbound_packet_digest": "sha256:packet"},
            {"invocation_id": "inv_rejected", "phase": "planning", "status": "failed", "reason_code": "preflight-required"},
        ],
        "provider_preflights": {"p": {"invocation_id": "inv_ok", "status": "consumed", "outbound_packet_digest": "sha256:packet"}},
        "canonical_plan": {"digest": "sha256:plan", "generation": 1},
        "executor_handoff": {"handoff_id": "h"},
        "decisions": [{"step_id": "s", "handoff_id": "h", "plan_digest": "sha256:plan", "plan_generation": 1}],
    }
    totals = reduce_planning_provider_kpis([state], population_kind="controlled")["totals"]
    assert totals["eligible_complex_planning_selection"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert totals["preflight_live_digest_match"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert totals["canonical_plan_executor_lineage"] == {"numerator": 1, "denominator": 1, "rate": 1.0}


def test_core_strategy_is_eligible_but_not_provider_selection_and_ignores_arbitrary_state_counters():
    state = {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "core",
             "ineligible_external_planning_invocations": 999, "dry_run_external_effect_count": 999,
             "authority_injection_accept_count": 999, "legacy_session_retroactive_provider_invocations": 999}
    result = reduce_planning_provider_kpis([state], population_kind="controlled")
    assert result["totals"]["eligible_complex_planning_selection"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert all(result["totals"][key] == 0 for key in (
        "ineligible_external_planning_invocations", "dry_run_external_effect_count",
        "authority_injection_accept_count", "legacy_session_retroactive_provider_invocations",
    ))


def test_reducer_counts_only_state_owned_external_effects_and_exact_preflight_binding():
    state = {
        "planning_policy_version": 1,
        "specialist_invocations": [
            {"invocation_id": "bad", "phase": "planning", "status": "running", "reason_code": "phase-not-allowed"},
            {"invocation_id": "live", "phase": "planning", "status": "completed", "input_outbound_packet_digest": "sha256:live"},
            {"invocation_id": "reserved", "phase": "planning", "status": "reserved", "input_outbound_packet_digest": "sha256:reserved"},
        ],
        "provider_preflights": {
            "dry": {"dry_run": True, "external_effect_evidence": {"count": 1}},
            "live": {"invocation_id": "live", "status": "consumed", "outbound_packet_digest": "sha256:drift"},
            "reserved": {"invocation_id": "reserved", "status": "consumed", "outbound_packet_digest": "sha256:reserved"},
        },
        "provider_plan_imports": {"bad": {"candidate": {"mission_metadata": {"authority": {"owner": "provider"}}}}},
    }
    totals = reduce_planning_provider_kpis([state], population_kind="controlled")["totals"]
    assert totals["ineligible_external_planning_invocations"] == 1
    assert totals["dry_run_external_effect_count"] == 1
    assert totals["preflight_live_digest_match"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert totals["authority_injection_accept_count"] == 1
    legacy = {"specialist_invocations": [{"phase": "planning", "status": "completed", "mode": "command-provider"}]}
    assert reduce_planning_provider_kpis([legacy], population_kind="controlled")["totals"]["legacy_session_retroactive_provider_invocations"] == 1


@pytest.mark.parametrize("mutate", [
    lambda value: value["totals"]["eligible_complex_planning_selection"].update({"numerator": 2, "denominator": 1}),
    lambda value: value["totals"]["eligible_complex_planning_selection"].update({"numerator": False}),
    lambda value: value["totals"].pop("authority_injection_accept_count"),
    lambda value: value.update({"schema": "mission-planning-provider-kpi/9"}),
    lambda value: value.update({"extra": True}),
    lambda value: value["reason_code_counts"].pop("fallback"),
    lambda value: value["cohorts"].append({"complexity": "Unknown"}),
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
