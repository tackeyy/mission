"""Issue #387: durable specialist selection and invocation checkpoints."""

import json

import pytest


def _read_state(root):
    return json.loads(
        (root / ".mission-state" / "sessions" / "test.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("complexity", ["Simple", "Standard", "Complex", "Critical"])
@pytest.mark.parametrize("role", ["implementer", "checker", "planning", "analyze", "release"])
def test_init_always_writes_a_pending_selection_checkpoint_for_every_profile_and_role(
    run_cli, tmp_path, complexity, role,
):
    run_cli(
        "init", "portable checkpoint mission", "--complexity", complexity,
        "--force-mission", "--role", role, cwd=tmp_path, check=True,
    )

    checkpoint = _read_state(tmp_path)["specialists_decision"]

    assert checkpoint["decision"] == "none"
    assert checkpoint["reason_code"] == "pending-evaluation"
    assert checkpoint["lifecycle_state"] == "candidate"
    assert checkpoint["selection_id"].startswith("sel_")
    assert len(checkpoint["selection_id"]) == 36


def test_recommendation_binds_candidates_and_selected_provider_to_one_checkpoint(
    run_cli, tmp_path
):
    run_cli("init", "checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots", "--task",
        "Implement backend API endpoint tests", "--installed-skills", "backend-provider",
        "--record-state", "--json", cwd=tmp_path, check=True,
    )
    state = _read_state(tmp_path)
    selection_id = state["specialists_decision"]["selection_id"]
    assert json.loads(result.stdout)["specialists_decision"]["selection_id"] == selection_id
    assert state["specialists_decision"]["decision"] == "selected"
    assert {x["selection_id"] for x in state["specialists_candidates"]} == {selection_id}
    assert {x["selection_id"] for x in state["specialists_selected"]} == {selection_id}


def test_started_invocation_transitions_in_place_to_terminal_with_same_identity(run_cli, tmp_path):
    run_cli("init", "checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)
    run_cli(
        "specialists", "recommend", "--no-default-skill-roots", "--task",
        "Implement backend API endpoint tests", "--installed-skills", "backend-provider",
        "--record-state", cwd=tmp_path, check=True,
    )
    started = run_cli(
        "specialists", "log-invocation", "--iteration", "1", "--phase", "execution",
        "--role", "backend", "--skill", "backend-provider", "--mode", "skill-tool",
        "--status", "started", "--json", cwd=tmp_path, check=True,
    )
    invocation_id = json.loads(started.stdout)["entry"]["invocation_id"]
    run_cli(
        "specialists", "log-invocation", "--invocation-id", invocation_id,
        "--iteration", "1", "--phase", "execution", "--role", "backend",
        "--skill", "backend-provider", "--mode", "skill-tool", "--status", "completed",
        cwd=tmp_path, check=True,
    )
    invocations = _read_state(tmp_path)["specialist_invocations"]
    assert len(invocations) == 1
    assert invocations[0]["invocation_id"] == invocation_id
    assert invocations[0]["lifecycle_state"] == "terminal"


def test_mark_passes_rejects_pending_checkpoint(state_dir, run_cli, read_state, push_provenance_score):
    state_file = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["specialists_decision"] = {
        "policy": "checkpoint", "action": "continue-core", "decision": "none",
        "reason_code": "pending-evaluation", "lifecycle_state": "candidate",
        "selection_id": "sel_0123456789abcdef0123456789abcdef",
    }
    state["task_profile"] = {"primary": "backend"}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    push_provenance_score(state_dir.parent)
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "selection checkpoint is not terminal" in result.stderr


def test_mark_passes_rejects_selected_checkpoint_without_terminal_evidence(
    state_dir, run_cli, read_state, push_provenance_score
):
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state_file = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({"task_profile": {"primary": "backend"},
        "specialists_decision": {"policy": "auto", "action": "select", "decision": "selected",
            "reason_code": "candidate-selected", "lifecycle_state": "selected", "selection_id": selection_id},
        "specialists_selected": [{"role": "backend", "skill": "backend-provider", "selection_id": selection_id}],
        "specialist_invocations": []})
    state_file.write_text(json.dumps(state), encoding="utf-8")
    push_provenance_score(state_dir.parent)
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "terminal specialist invocation missing" in result.stderr


def test_mark_passes_rejects_selected_decision_without_current_selected_record(
    state_dir, run_cli, read_state, push_provenance_score
):
    """A selected decision cannot bypass the terminal-evidence gate with an empty set."""
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state_file = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({"task_profile": {"primary": "backend"},
        "specialists_decision": {"policy": "auto", "action": "select", "decision": "selected",
            "reason_code": "candidate-selected", "lifecycle_state": "selected", "selection_id": selection_id},
        "specialists_candidates": [], "specialists_selected": [], "specialist_invocations": []})
    state_file.write_text(json.dumps(state), encoding="utf-8")
    push_provenance_score(state_dir.parent)
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "selected checkpoint has no current selected provider" in result.stderr


def test_waiver_records_current_selection_identity_and_allows_selected_gap(
    state_dir, run_cli, read_state, push_provenance_score
):
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state_file = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({"task_profile": {"primary": "backend"},
        "specialists_decision": {"policy": "auto", "action": "select", "decision": "selected",
            "reason_code": "candidate-selected", "lifecycle_state": "selected", "selection_id": selection_id},
        "specialists_selected": [{"role": "backend", "skill": "backend-provider", "selection_id": selection_id}],
        "specialist_invocations": []})
    state_file.write_text(json.dumps(state), encoding="utf-8")
    push_provenance_score(state_dir.parent)
    result = run_cli("mark-passes", "--specialist-waiver", "out of scope", cwd=state_dir.parent)
    assert result.returncode == 0, result.stderr
    waiver = read_state(state_dir)["specialist_waiver"]
    assert waiver["selection_id"] == selection_id
    assert waiver["reason"] == "out of scope"


def test_public_contract_rejects_duplicate_invocation_identity():
    from specialist_lifecycle import (
        SpecialistLifecycleError, invocation_by_id, is_terminal_invocation,
        selection_checkpoint, validate_invocation_transition, validate_specialist_lifecycle,
    )
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    invocation = {
        "invocation_id": "inv_0123456789abcdef0123456789abcdef", "selection_id": selection_id,
        "iteration": 1, "phase": "review", "role": "reviewer", "skill": "provider",
        "mode": "skill-tool", "status": "completed", "lifecycle_state": "terminal",
    }
    state = {"specialists_decision": {"decision": "selected", "reason_code": "candidate-selected",
             "lifecycle_state": "selected", "selection_id": selection_id},
             "specialists_candidates": [{"skill": "provider", "selection_id": selection_id}],
             "specialists_selected": [{"skill": "provider", "selection_id": selection_id}],
             "specialist_invocations": [invocation, dict(invocation)]}
    with pytest.raises(SpecialistLifecycleError):
        validate_specialist_lifecycle(state)
    with pytest.raises(SpecialistLifecycleError):
        invocation_by_id(state, invocation["invocation_id"])
    state["specialist_invocations"] = [invocation]
    assert selection_checkpoint(state)["selection_id"] == selection_id
    assert invocation_by_id(state, invocation["invocation_id"]) == invocation
    assert is_terminal_invocation(invocation)
    with pytest.raises(SpecialistLifecycleError):
        validate_invocation_transition(invocation, invocation)


def test_command_provider_propagates_selection_and_invocation_ids(run_cli, tmp_path):
    run_cli("init", "command checkpoint", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = _read_state(tmp_path)
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state["specialists_decision"] = {"policy": "auto", "action": "select", "decision": "selected",
        "reason_code": "candidate-selected", "lifecycle_state": "selected", "selection_id": selection_id}
    state["specialists_selected"] = [{"role": "command-review", "skill": "command-provider",
        "kind": "command", "command": "true", "selection_id": selection_id}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_cli("specialists", "invoke-command", "--provider", "command-provider",
                     "--iteration", "1", "--phase", "review", "--json", cwd=tmp_path, check=True)
    entry = json.loads(result.stdout)["entry"]
    assert entry["selection_id"] == selection_id
    assert entry["invocation_id"].startswith("inv_")
    assert entry["lifecycle_state"] == "terminal"
