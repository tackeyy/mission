import sys
import importlib.util
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


def _provider_import_fixture(run_cli, tmp_path):
    fixture_path = Path(__file__).with_name("test_plan_import.py")
    spec = importlib.util.spec_from_file_location("issue398_plan_import_fixture", fixture_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None; spec.loader.exec_module(module)
    registry, state_file, result, invocation, env = module._setup(run_cli, tmp_path)
    state = __import__("json").loads(state_file.read_text())
    state["planning_policy_version"] = 1
    state["planning_strategy"] = "provider-primary"
    state_file.write_text(__import__("json").dumps(state))
    source = tmp_path / "provider-result.json"; source.write_text(__import__("json").dumps(result))
    return registry, state_file, source, invocation, env


def test_provider_import_promote_advance_and_handoff_preserves_identity(run_cli, tmp_path):
    registry, state_file, source, invocation, env = _provider_import_fixture(run_cli, tmp_path)
    assert run_cli("specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), cwd=tmp_path, env_extra=env).returncode == 0
    assert run_cli("planning", "promote-provider-plan", "--invocation-id", invocation, cwd=tmp_path, env_extra=env).returncode == 0
    assert run_cli("advance", "--phase", "executing", cwd=tmp_path, env_extra=env).returncode == 0
    state = __import__("json").loads(state_file.read_text())
    assert state["canonical_plan"]["source_id"] == invocation
    assert state["executor_handoff"]["plan_digest"] == state["canonical_plan"]["digest"]
    assert state["executor_handoff"]["plan_generation"] == state["canonical_plan"]["generation"]


def test_advance_publish_fault_rolls_back_phase_and_handoff(monkeypatch, run_cli, tmp_path):
    run_cli("init", "atomic", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, _plan = _canonical_core_state(tmp_path)
    before = state_file.read_bytes()
    cli_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue398_advance_fault", cli_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None; spec.loader.exec_module(module)
    original = module.atomic_write_json
    def fail(path, data, **kwargs):
        if path == state_file and data.get("executor_handoff"):
            raise OSError("simulated advance publish failure")
        return original(path, data, **kwargs)
    monkeypatch.chdir(tmp_path); monkeypatch.setenv("MISSION_SESSION_ID", "test"); monkeypatch.setattr(module, "atomic_write_json", fail)
    monkeypatch.setattr(sys, "argv", [str(cli_path), "advance", "--phase", "executing"])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1
    assert state_file.read_bytes() == before
