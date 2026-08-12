import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from planning_lifecycle import derive_planning_lifecycle


def _canonical_core_state(tmp_path):
    state_file = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = __import__("json").loads(state_file.read_text())
    plan = tmp_path / ".mission-state" / "plans" / "canonical.json"; plan.parent.mkdir(exist_ok=True)
    payload = {"schema": "mission-plan/1", "steps": [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1"]}]}
    raw = __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode(); plan.write_bytes(raw)
    binding = {"generation": 1, "source": "core", "source_id": "planner-1", "selection_source": "automatic", "iteration": 1}
    state["canonical_plan"] = {"path": str(plan.relative_to(tmp_path)), "digest": "sha256:" + __import__("hashlib").sha256(raw).hexdigest(), **binding}
    state["planning_source_records"] = {"core:planner-1": binding}
    state_file.write_text(__import__("json").dumps(state))
    return state_file, plan


def test_policy_v1_primary_without_invocation_prepares_one_safe_action():
    state = {
        "planning_policy_version": 1,
        "phase": "planning",
        "iteration": 1,
        "planning_strategy": "provider-primary",
        "specialists_selected": [{"provider_id": "planner"}],
        "specialist_invocations": [],
    }
    assert derive_planning_lifecycle(state)["next_action"] == "prepare-planning-provider"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("running", "reconcile-provider-invocation"), ("completed", "import-planning-result")],
)
def test_running_invocation_reconciles_before_any_new_action(status, expected):
    state = {
        "planning_policy_version": 1, "phase": "planning", "iteration": 1,
        "planning_strategy": "provider-primary",
        "specialist_invocations": [{"invocation_id": "inv_" + "a" * 32, "phase": "planning", "iteration": 1, "status": status}],
    }
    assert derive_planning_lifecycle(state)["next_action"] == expected


def test_legacy_policy_absent_stays_core_without_provider_action():
    state = {"phase": "planning", "iteration": 1, "specialist_invocations": []}
    result = derive_planning_lifecycle(state)
    assert result["mode"] == "legacy-core"
    assert result["next_action"] == "run-planner"


def test_policy_v1_never_returns_a_cross_gate_command_sequence(run_cli, tmp_path):
    run_cli("init", "provider plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    response = run_cli("next", cwd=tmp_path)
    assert response.returncode == 0
    result = __import__("json").loads(response.stdout)
    assert result["next_action"] == "run-planner"
    assert "command_sequence" not in result


def test_advance_executing_rejects_policy_v1_without_canonical_plan(run_cli, tmp_path):
    run_cli("init", "provider plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    response = run_cli("advance", "--phase", "executing", cwd=tmp_path)
    assert response.returncode == 2


def test_executor_handoff_rejects_plan_generation_or_source_drift(run_cli, tmp_path):
    run_cli("init", "provider plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, _plan = _canonical_core_state(tmp_path)
    state = __import__("json").loads(state_file.read_text())
    state["planning_source_records"]["core:planner-1"]["generation"] = 2
    state_file.write_text(__import__("json").dumps(state))
    response = run_cli("advance", "--phase", "executing", cwd=tmp_path)
    assert response.returncode == 2
    assert __import__("json").loads(state_file.read_text())["phase"] == "planning"


def test_atomic_advance_handoff_and_mutation_rejects_before_step(run_cli, tmp_path):
    run_cli("init", "provider plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, plan = _canonical_core_state(tmp_path)
    assert run_cli("advance", "--phase", "executing", cwd=tmp_path).returncode == 0
    assert run_cli("executor-handoff", "begin", cwd=tmp_path).returncode == 0
    plan.write_text('{"schema":"mission-plan/1","steps":[]}')
    response = run_cli("executor-handoff", "verify-step", "--step-id", "s1", cwd=tmp_path)
    state = __import__("json").loads(state_file.read_text())
    assert response.returncode == 2
    assert not state["decisions"] and state["executor_handoff"]["status"] == "rejected"


def test_legacy_reselection_is_explicit_and_drops_unsafe_raw_records(run_cli, tmp_path):
    run_cli("init", "legacy", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = __import__("json").loads(state_file.read_text())
    state.pop("planning_policy_version")
    state["specialists_candidates"] = [{"auto_use": {"private": "raw"}}]
    state_file.write_text(__import__("json").dumps(state))
    assert __import__("json").loads(run_cli("next", cwd=tmp_path).stdout)["next_action"] == "run-planner"
    response = run_cli("planning", "reselect", cwd=tmp_path)
    migrated = __import__("json").loads(state_file.read_text())
    assert response.returncode == 0
    assert migrated["planning_policy_version"] == 1 and migrated["specialists_candidates"] == []
    assert "auto_use" not in state_file.read_text()


@pytest.mark.parametrize(("required", "expected"), [(False, "run-planner"), (True, "halt-required-planning-provider")])
def test_terminal_provider_failure_has_deterministic_optional_or_required_outcome(required, expected):
    state = {"planning_policy_version": 1, "phase": "planning", "iteration": 1,
             "planning_strategy": "provider-primary",
             "specialist_invocations": [{"phase": "planning", "iteration": 1, "status": "failed", "required": required}]}
    assert derive_planning_lifecycle(state)["next_action"] == expected


def test_handoff_rejects_duplicate_and_dependency_before_lineage_record(run_cli, tmp_path):
    run_cli("init", "provider plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, _plan = _canonical_core_state(tmp_path)
    assert run_cli("advance", "--phase", "executing", cwd=tmp_path).returncode == 0
    assert run_cli("executor-handoff", "begin", cwd=tmp_path).returncode == 0
    assert run_cli("executor-handoff", "begin", cwd=tmp_path).returncode == 2
    assert run_cli("executor-handoff", "record-step", "--step-id", "s2", "--result", "ok", cwd=tmp_path).returncode == 2
    assert run_cli("executor-handoff", "record-step", "--step-id", "s1", "--result", "ok", cwd=tmp_path).returncode == 0
    assert run_cli("executor-handoff", "record-step", "--step-id", "s1", "--result", "ok", cwd=tmp_path).returncode == 2
    assert run_cli("executor-handoff", "record-step", "--step-id", "s2", "--result", "ok", cwd=tmp_path).returncode == 0
    assert run_cli("executor-handoff", "complete", cwd=tmp_path).returncode == 0
    decisions = __import__("json").loads(state_file.read_text())["decisions"]
    assert {entry["step_id"] for entry in decisions} == {"s1", "s2"}
    assert all(entry["plan_source"] == "core" and entry["plan_generation"] == 1 for entry in decisions)
