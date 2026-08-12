"""#399 conformance projections reuse #394-#398 state contracts, not new E2E flows."""
import json
import sys
import importlib.util
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))
from planning_provider_metrics import reduce_planning_provider_kpis


@pytest.mark.parametrize("name,state,assertion", [
    ("simple-floor", {"planning_policy_version": 1, "complexity": "Simple", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("unknown-floor", {"planning_policy_version": 1, "complexity": "Unknown", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("complex-primary", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "provider-primary"}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 1.0),
    ("optional-fallback", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "core", "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "provider-unavailable"}]}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 0.0),
    ("approval-wait", {"planning_policy_version": 1, "provider_preflights": {"p": {"status": "awaiting-approval"}}}, lambda t: t["preflight_live_digest_match"]["denominator"] == 0),
    ("invalid-plan", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "invalid-plan"}]}, lambda t: t["canonical_plan_executor_lineage"]["denominator"] == 0),
    ("legacy", {"complexity": "Complex", "legacy_session_retroactive_provider_invocations": 0}, lambda t: t["legacy_session_retroactive_provider_invocations"] == 0),
])
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
