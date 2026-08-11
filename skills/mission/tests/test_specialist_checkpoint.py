"""Issue #387: durable specialist selection and invocation checkpoints."""

import json
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from specialist_lifecycle import (
    SpecialistLifecycleError,
    validate_selection_checkpoint,
    validate_specialist_lifecycle,
)


def _read_state(root):
    return json.loads(
        (root / ".mission-state" / "sessions" / "test.json").read_text(encoding="utf-8")
    )


def _push_passing_score(run_cli, root):
    result = run_cli(
        "push-score",
        "--iteration",
        "1",
        "--composite",
        "4.3",
        "--min-item",
        "4.3",
        "--items",
        json.dumps({"mission_achievement": 4.3, "accuracy": 4.3}),
        cwd=root,
    )
    assert result.returncode == 0, result.stderr


def test_init_always_writes_a_pending_selection_checkpoint(run_cli, tmp_path):
    run_cli(
        "init",
        "portable checkpoint mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )

    state = _read_state(tmp_path)
    checkpoint = state["specialists_decision"]

    assert checkpoint["decision"] == "none"
    assert checkpoint["reason_code"] == "pending-evaluation"
    assert checkpoint["lifecycle_state"] == "candidate"
    assert checkpoint["selection_id"].startswith("sel_")
    assert len(checkpoint["selection_id"]) == 36


def test_recorded_recommendation_binds_candidates_and_selection_to_one_id(run_cli, tmp_path):
    run_cli(
        "init",
        "specialist lifecycle mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Implement backend API endpoint tests",
        "--installed-skills",
        "backend-provider",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    output = json.loads(result.stdout)
    state = _read_state(tmp_path)
    selection_id = state["specialists_decision"]["selection_id"]

    assert state["specialists_decision"]["decision"] == "selected"
    assert state["specialists_decision"]["lifecycle_state"] == "selected"
    assert output["specialists_decision"]["selection_id"] == selection_id
    assert {item["selection_id"] for item in state["specialists_candidates"]} == {selection_id}
    assert {item["selection_id"] for item in state["specialists_selected"]} == {selection_id}


def test_terminal_invocation_is_bound_to_selection_and_has_an_opaque_id(run_cli, tmp_path):
    run_cli(
        "init",
        "specialist lifecycle mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Implement backend API endpoint tests",
        "--installed-skills",
        "backend-provider",
        "--record-state",
        cwd=tmp_path,
        check=True,
    )
    run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "execution",
        "--role",
        "backend",
        "--skill",
        "backend-provider",
        "--mode",
        "skill-tool",
        "--status",
        "completed",
        cwd=tmp_path,
        check=True,
    )

    state = _read_state(tmp_path)
    checkpoint = state["specialists_decision"]
    invocation = state["specialist_invocations"][0]

    assert invocation["selection_id"] == checkpoint["selection_id"]
    assert invocation["invocation_id"].startswith("inv_")
    assert len(invocation["invocation_id"]) == 36
    assert invocation["lifecycle_state"] == "terminal"


def test_started_invocation_transitions_in_place_to_terminal_result(run_cli, tmp_path):
    run_cli(
        "init",
        "specialist lifecycle mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Implement backend API endpoint tests",
        "--installed-skills",
        "backend-provider",
        "--record-state",
        cwd=tmp_path,
        check=True,
    )
    started = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "execution",
        "--role",
        "backend",
        "--skill",
        "backend-provider",
        "--mode",
        "skill-tool",
        "--status",
        "started",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    invocation_id = json.loads(started.stdout)["entry"]["invocation_id"]

    run_cli(
        "specialists",
        "log-invocation",
        "--invocation-id",
        invocation_id,
        "--iteration",
        "1",
        "--phase",
        "execution",
        "--role",
        "backend",
        "--skill",
        "backend-provider",
        "--mode",
        "skill-tool",
        "--status",
        "completed",
        cwd=tmp_path,
        check=True,
    )

    invocations = _read_state(tmp_path)["specialist_invocations"]
    assert len(invocations) == 1
    assert invocations[0]["invocation_id"] == invocation_id
    assert invocations[0]["status"] == "completed"
    assert invocations[0]["lifecycle_state"] == "terminal"


def test_mark_passes_rejects_selected_checkpoint_without_terminal_invocation(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(
        {
            "created_at_session": "2026-08-10T00:00:00Z",
            "started_at": "2026-08-10T00:00:00Z",
            "task_profile": {"primary": "backend"},
            "specialists_decision": {
                "policy": "auto",
                "action": "select",
                "decision": "selected",
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
                "selection_id": "sel_0123456789abcdef0123456789abcdef",
            },
            "specialists_selected": [
                {
                    "role": "backend",
                    "skill": "backend-provider",
                    "status": "selected",
                    "selection_id": "sel_0123456789abcdef0123456789abcdef",
                }
            ],
            "specialist_invocations": [],
        }
    )
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _push_passing_score(run_cli, state_dir.parent)

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "terminal specialist invocation" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_rejects_pending_selection_checkpoint(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(
        {
            "created_at_session": "2026-08-10T00:00:00Z",
            "started_at": "2026-08-10T00:00:00Z",
            "task_profile": {"primary": "backend"},
            "specialists_decision": {
                "policy": "checkpoint",
                "action": "continue-core",
                "decision": "none",
                "reason_code": "pending-evaluation",
                "lifecycle_state": "candidate",
                "selection_id": "sel_0123456789abcdef0123456789abcdef",
            },
        }
    )
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _push_passing_score(run_cli, state_dir.parent)

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "selection checkpoint is not terminal" in result.stderr
    assert read_state(state_dir)["passes"] is False


def test_explicit_specialist_waiver_allows_pass_and_records_selection_id(
    state_dir, run_cli, read_state
):
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(
        {
            "created_at_session": "2026-08-10T00:00:00Z",
            "started_at": "2026-08-10T00:00:00Z",
            "task_profile": {"primary": "backend"},
            "specialists_decision": {
                "policy": "auto",
                "action": "select",
                "decision": "selected",
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
                "selection_id": selection_id,
            },
            "specialists_selected": [
                {
                    "role": "backend",
                    "skill": "backend-provider",
                    "status": "selected",
                    "selection_id": selection_id,
                }
            ],
            "specialist_invocations": [],
        }
    )
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _push_passing_score(run_cli, state_dir.parent)

    result = run_cli(
        "mark-passes",
        "--specialist-waiver",
        "provider was not needed after the task scope narrowed",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    persisted = read_state(state_dir)
    assert persisted["passes"] is True
    assert persisted["specialist_waiver"]["selection_id"] == selection_id
    assert persisted["specialist_waiver"]["reason"] == (
        "provider was not needed after the task scope narrowed"
    )


def test_confirmed_selection_promotes_checkpoint_and_keeps_selection_identity(
    state_dir, run_cli, read_state
):
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(
        {
            "specialists_decision": {
                "policy": "confirm",
                "action": "ask-user",
                "decision": "none",
                "reason_code": "awaiting-confirmation",
                "lifecycle_state": "candidate",
                "prompted_user": True,
                "selection_id": selection_id,
            },
            "specialists_candidates": [
                {
                    "role": "reviewer",
                    "skill": "review-provider",
                    "status": "available",
                    "selection_id": selection_id,
                }
            ],
            "specialists_selected": [],
        }
    )
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "review",
        "--role",
        "reviewer",
        "--skill",
        "review-provider",
        "--mode",
        "codex-inline",
        "--status",
        "inline-applied",
        "--selection-source",
        "confirmed-user",
        cwd=state_dir.parent,
        check=True,
    )

    persisted = read_state(state_dir)
    assert persisted["specialists_decision"]["decision"] == "selected"
    assert persisted["specialists_decision"]["lifecycle_state"] == "selected"
    assert persisted["specialists_selected"][0]["selection_id"] == selection_id
    assert persisted["specialist_invocations"][0]["selection_id"] == selection_id


@pytest.mark.parametrize(
    "checkpoint",
    [
        {},
        {
            "decision": "none",
            "reason_code": "no-candidates",
            "lifecycle_state": "terminal",
            "selection_id": "../../not-an-id",
        },
        {
            "decision": "unexpected",
            "reason_code": "no-candidates",
            "lifecycle_state": "terminal",
            "selection_id": "sel_0123456789abcdef0123456789abcdef",
        },
        {
            "decision": "none",
            "reason_code": "no-candidates",
            "lifecycle_state": "candidate",
            "selection_id": "sel_0123456789abcdef0123456789abcdef",
        },
    ],
)
def test_selection_checkpoint_validator_rejects_malformed_or_inconsistent_input(checkpoint):
    with pytest.raises(SpecialistLifecycleError):
        validate_selection_checkpoint(checkpoint)


def test_command_provider_terminal_record_uses_checkpoint_and_invocation_ids(
    run_cli, tmp_path
):
    run_cli(
        "init",
        "command provider lifecycle mission",
        "--complexity",
        "Complex",
        cwd=tmp_path,
        check=True,
    )
    helper = tmp_path / "provider.py"
    helper.write_text("print('review result: ' + 'x' * 250)\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "documentation-reviewer",
                        "skill": "documentation-review-provider",
                        "kind": "command",
                        "command": sys.executable,
                        "args": [str(helper)],
                        "task_profiles": ["documentation"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review README documentation",
        "--complexity",
        "Complex",
        "--record-state",
        cwd=tmp_path,
        check=True,
    )
    run_cli(
        "specialists",
        "invoke-command",
        "--provider",
        "documentation-review-provider",
        "--iteration",
        "1",
        "--phase",
        "review",
        cwd=tmp_path,
        check=True,
    )

    state = _read_state(tmp_path)
    invocation = state["specialist_invocations"][0]
    assert invocation["selection_id"] == state["specialists_decision"]["selection_id"]
    assert invocation["invocation_id"].startswith("inv_")
    assert invocation["lifecycle_state"] == "terminal"


def test_lifecycle_validator_rejects_duplicate_invocation_ids_and_stale_selection_links():
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    invocation = {
        "invocation_id": "inv_0123456789abcdef0123456789abcdef",
        "selection_id": "sel_abcdef0123456789abcdef0123456789",
        "iteration": 1,
        "phase": "review",
        "role": "reviewer",
        "skill": "review-provider",
        "mode": "skill-tool",
        "status": "completed",
        "lifecycle_state": "terminal",
    }
    state = {
        "specialists_decision": {
            "decision": "selected",
            "reason_code": "candidate-selected",
            "lifecycle_state": "selected",
            "selection_id": selection_id,
        },
        "specialists_candidates": [
            {"skill": "review-provider", "selection_id": selection_id}
        ],
        "specialists_selected": [
            {"skill": "review-provider", "selection_id": selection_id}
        ],
        "specialist_invocations": [invocation, dict(invocation)],
    }

    with pytest.raises(SpecialistLifecycleError):
        validate_specialist_lifecycle(state)
