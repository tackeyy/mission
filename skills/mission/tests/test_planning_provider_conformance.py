"""#399 conformance projections reuse #394-#398 state contracts, not new E2E flows."""
import json
import sys
import importlib.util
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))
from planning_provider_metrics import reduce_planning_provider_kpis


CONFORMANCE_CASES = [
    ("simple-floor", {"planning_policy_version": 1, "complexity": "Simple", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("unknown-floor", {"planning_policy_version": 1, "complexity": "Unknown", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("complex-primary", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "provider-primary"}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 1.0),
    ("optional-fallback", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "core", "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "provider-unavailable"}]}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 0.0),
    ("approval-wait", {"planning_policy_version": 1, "provider_preflights": {"p": {"status": "awaiting-approval"}}}, lambda t: t["preflight_live_digest_match"]["denominator"] == 0),
    ("invalid-plan", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "invalid-plan"}]}, lambda t: t["canonical_plan_executor_lineage"]["denominator"] == 0),
    ("legacy", {"complexity": "Complex", "legacy_session_retroactive_provider_invocations": 0}, lambda t: t["legacy_session_retroactive_provider_invocations"] == 0),
    ("standard-core", {"planning_policy_version": 1, "complexity": "Standard", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("critical-advisory", {"planning_policy_version": 1, "complexity": "Critical", "planning_strategy": "provider-advisory"}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 1.0),
    ("required-halt", {"planning_policy_version": 1, "planning_provider_required": True, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "provider-unavailable"}]}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("preflight-reserved-excluded", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "reserved"}]}, lambda t: t["preflight_live_digest_match"]["denominator"] == 0),
    ("preflight-running-digest-match", {"planning_policy_version": 1, "specialist_invocations": [{"invocation_id": "i", "phase": "planning", "status": "running", "input_outbound_packet_digest": "sha256:x"}], "provider_preflights": {"p": {"invocation_id": "i", "status": "consumed", "outbound_packet_digest": "sha256:x"}}}, lambda t: t["preflight_live_digest_match"]["rate"] == 1.0),
    ("preflight-digest-drift", {"planning_policy_version": 1, "specialist_invocations": [{"invocation_id": "i", "phase": "planning", "status": "completed", "input_outbound_packet_digest": "sha256:x"}], "provider_preflights": {"p": {"invocation_id": "i", "status": "consumed", "outbound_packet_digest": "sha256:y"}}}, lambda t: t["preflight_live_digest_match"]["rate"] == 0.0),
    ("preflight-required-rejection", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "preflight-required"}]}, lambda t: t["preflight_live_digest_match"]["denominator"] == 0),
    ("ambient-authority-rejected", {"planning_policy_version": 1, "provider_plan_imports": {"i": {"candidate": {"mission_metadata": {"authority": {"owner": "provider"}}}}}}, lambda t: t["authority_injection_accept_count"] == 0),
    ("orphan-import-no-lineage", {"planning_policy_version": 1, "decisions": [{"step_id": "s"}]}, lambda t: t["canonical_plan_executor_lineage"]["rate"] == 0.0),
    ("executor-lineage-match", {"planning_policy_version": 1, "canonical_plan": {"digest": "sha256:p", "generation": 1}, "executor_handoff": {"handoff_id": "h"}, "decisions": [{"step_id": "s", "handoff_id": "h", "plan_digest": "sha256:p", "plan_generation": 1}]}, lambda t: t["canonical_plan_executor_lineage"]["rate"] == 1.0),
    ("executor-mutation-step-zero", {"planning_policy_version": 1, "canonical_plan": {"digest": "sha256:p", "generation": 1}, "executor_handoff": {"handoff_id": "h"}, "decisions": []}, lambda t: t["canonical_plan_executor_lineage"]["denominator"] == 0),
    ("dual-v1-v2-core", {"planning_policy_version": 1, "planning_strategy": "core", "complexity": "Complex"}, lambda t: t["eligible_complex_planning_selection"]["numerator"] == 0),
    ("legacy-provider-invocation", {"specialist_invocations": [{"phase": "planning", "status": "completed", "mode": "command-provider"}]}, lambda t: t["legacy_session_retroactive_provider_invocations"] == 1),
    ("dry-run-no-effect", {"planning_policy_version": 1, "provider_preflights": {"p": {"dry_run": True}}}, lambda t: t["dry_run_external_effect_count"] == 0),
    ("dry-run-effect-evidence", {"planning_policy_version": 1, "provider_preflights": {"p": {"dry_run": True, "external_effect_evidence": {"count": 1}}}}, lambda t: t["dry_run_external_effect_count"] == 0),
    ("phase-ineligible-after-start", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "running", "reason_code": "phase-not-allowed"}]}, lambda t: t["ineligible_external_planning_invocations"] == 1),
    ("application-ineligible-before-start", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "provider-not-selected"}]}, lambda t: t["ineligible_external_planning_invocations"] == 0),
]

# Issue #399 matrix rows map to production assertions; the reducer cases above
# remain unit coverage, while this inventory is executed as targeted nodeids.
PRODUCTION_NODEIDS = [
    "skills/mission/tests/test_planning_provider_eligibility.py::test_complexity_floor_is_a_hard_gate_for_explicit_selection",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_primary_planning_mode_requires_structured_result_contract",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_v2_empty_auto_use_mixed_with_activation_fails_closed",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_unparseable_higher_input_blocks_lower_registry_and_builtin_candidates",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_v2_provider_id_only_tombstone_suppresses_builtin_provider",
    "skills/mission/tests/test_provider_preflight.py::test_prepare_invocation_publishes_private_packet_and_state_pointer_without_spawn",
    "skills/mission/tests/test_provider_preflight.py::test_host_verified_receipt_runs_exact_packet_once_and_rejects_replay",
    "skills/mission/tests/test_provider_preflight.py::test_input_byte_mutation_after_approval_blocks_spawn",
    "skills/mission/tests/test_provider_preflight.py::test_strict_host_backend_requires_all_attested_capabilities_before_spawn",
    "skills/mission/tests/test_provider_preflight.py::test_dispatch_prepared_packet_never_routes_strict_bytes_to_ambient_callable",
    "skills/mission/tests/test_provider_preflight.py::test_strict_preflight_never_falls_back_to_plain_spawn_without_live_isolator",
    "skills/mission/tests/test_plan_import.py::test_plan_import_publishes_raw_canonical_and_bound_state",
    "skills/mission/tests/test_plan_import.py::test_invalid_plan_input_preserves_state_and_no_candidate",
    "skills/mission/tests/test_plan_import.py::test_import_rejects_noncurrent_invocation_or_unproven_consumed_preflight",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_running_invocation_reconciles_before_any_new_action",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_legacy_reselection_is_explicit_and_drops_unsafe_raw_records",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_terminal_provider_failure_has_deterministic_optional_or_required_outcome",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_provider_import_promote_advance_and_handoff_preserves_identity",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_atomic_advance_handoff_and_mutation_rejects_before_step",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_handoff_rejects_duplicate_and_dependency_before_lineage_record",
    "skills/mission/tests/test_planning_provider_lifecycle.py::test_advance_publish_fault_rolls_back_phase_and_handoff",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_registry_docs_define_v2_activation_and_selection_provenance",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_runtime_cli_cannot_claim_automatic_selection_source",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_v2_complexity_provider_requires_an_allowed_planning_phase",
    "skills/mission/tests/test_planning_provider_eligibility.py::test_legacy_v1_loader_ignores_v2_root_when_explicitly_given",
]


def test_issue_matrix_maps_all_rows_to_unique_production_nodeids():
    assert len(PRODUCTION_NODEIDS) == 25
    assert len(set(PRODUCTION_NODEIDS)) == 25
    assert all("::test_" in nodeid for nodeid in PRODUCTION_NODEIDS)


@pytest.mark.parametrize("name,state,assertion", CONFORMANCE_CASES)
def test_existing_lifecycle_state_contracts_reduce_to_conformance_kpis(name, state, assertion):
    totals = reduce_planning_provider_kpis([state], population_kind="controlled")["totals"]
    assert assertion(totals), name


@pytest.mark.parametrize("complexity", ["Simple", "Unknown"])
def test_real_cli_floor_recommendation_has_no_provider_selection(run_cli, tmp_path, complexity):
    # Standard init creates durable state; the recommendation's explicit
    # complexity is the contract under test (Simple init intentionally inlines).
    run_cli("init", "floor", "--complexity", "Standard", cwd=tmp_path, check=True)
    response = run_cli(
        "specialists", "recommend", "--no-default-skill-roots", "--task", "bounded task",
        "--complexity", complexity, "--record-state", "--json", cwd=tmp_path,
    )
    # No eligible provider is a deterministic recommendation outcome, not an
    # invocation failure; CLI reports it with its existing non-zero contract.
    assert response.returncode == 2, response.stderr
    recommendation = json.loads(response.stdout)
    assert recommendation["specialists_candidates"] == []
    assert recommendation["specialists_selected"] == []
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["specialists_selected"] == []


def test_real_cli_complex_primary_recommendation_selects_contract_bound_provider(run_cli, tmp_path):
    import os

    fixture_path = Path(__file__).with_name("test_plan_import.py")
    spec = importlib.util.spec_from_file_location("issue399_plan_import_fixture", fixture_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    commands = tmp_path / "commands"
    commands.mkdir()
    command = commands / "portable-plan-provider"
    command.write_text("#!/bin/sh\n")
    command.chmod(0o700)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "portable-provider", "role": "deep-planning", "skill": "portable-plan-provider",
        "kind": "command", "command": "portable-plan-provider", "args": [], "env": {},
        "task_profiles": ["architecture"], "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
        "planning": {"mode": "primary"}, "result_contract": module._contract(),
    }]}))
    run_cli("init", "primary", "--complexity", "Complex", cwd=tmp_path, check=True)
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture",
        "--registry", str(registry), "--complexity", "Complex", "--record-state", "--json",
        cwd=tmp_path, env_extra={"PATH": f"{commands}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    selected = payload["specialists_selected"]
    assert len(selected) == 1
    assert selected[0]["provider_id"] == "portable-provider"
    assert selected[0]["planning_mode"] == "primary"
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["planning_provider_binding"]["provider_id"] == "portable-provider"
    assert state["planning_provider_binding"]["selection_id"] == selected[0]["selection_id"]


def test_real_cli_provider_promote_handoff_rejects_mutated_step_without_lineage(run_cli, tmp_path):
    lifecycle_path = Path(__file__).with_name("test_planning_provider_lifecycle.py")
    spec = importlib.util.spec_from_file_location("issue399_lifecycle_fixture", lifecycle_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    registry, state_file, source, invocation, env = module._provider_import_fixture(run_cli, tmp_path)
    assert run_cli("specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), cwd=tmp_path, env_extra=env).returncode == 0
    assert run_cli("planning", "promote-provider-plan", "--invocation-id", invocation, cwd=tmp_path, env_extra=env).returncode == 0
    assert run_cli("advance", "--phase", "executing", cwd=tmp_path, env_extra=env).returncode == 0
    state = json.loads(state_file.read_text())
    assert state["canonical_plan"]["source_id"] == invocation
    assert run_cli("executor-handoff", "begin", cwd=tmp_path, env_extra=env).returncode == 0
    plan_path = tmp_path / state["canonical_plan"]["path"]
    plan_path.write_text('{"schema":"mission-plan/1","steps":[]}')
    assert run_cli("executor-handoff", "verify-step", "--step-id", "s", cwd=tmp_path, env_extra=env).returncode == 2
    mutated = json.loads(state_file.read_text())
    assert mutated["executor_handoff"]["plan_digest"] == state["canonical_plan"]["digest"]
    assert mutated["decisions"] == []
    totals = reduce_planning_provider_kpis([mutated], population_kind="controlled")["totals"]
    assert totals["canonical_plan_executor_lineage"]["denominator"] == 0
