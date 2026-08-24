"""Issue #659: specialist selection checkpoints have explicit terminal paths."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def run_cli(legacy_run_cli):
    """Exercise the retained-v4 compatibility path with deterministic state bytes."""
    return legacy_run_cli


def _write_confirmation_registry(root) -> None:
    registry = root / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "documentation-provider",
                        "skill": "documentation-provider",
                        "enabled": False,
                    },
                    {
                        "role": "frontend-provider",
                        "skill": "frontend-provider",
                        "enabled": False,
                    },
                    {
                        "role": "visual-quality-provider",
                        "skill": "visual-quality-provider",
                        "enabled": False,
                    },
                    {
                        "role": "external-reviewer",
                        "skill": "external-review-provider",
                        "kind": "command",
                        "command": "true",
                        "args": [],
                        "task_profiles": ["frontend"],
                        "confirm": True,
                        "risk": {"first_use_confirmation": True},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _recommend(run_cli, root, consent_file):
    return run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Update README React",
        "--installed-skills",
        "external-review-provider",
        "--consent-file",
        str(consent_file),
        "--record-state",
        "--json",
        cwd=root,
    )


def _read_state(root):
    return json.loads(
        (root / ".mission-state" / "sessions" / "test.json").read_text(
            encoding="utf-8"
        )
    )


def test_consented_confirmation_provider_can_finish_as_terminal_none(run_cli, tmp_path):
    _write_confirmation_registry(tmp_path)
    consent_file = tmp_path / "provider-consent.json"
    run_cli("init", "selection checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)
    run_cli(
        "specialists",
        "consent",
        "--provider",
        "external-review-provider",
        "--consent-file",
        str(consent_file),
        cwd=tmp_path,
        check=True,
    )

    result = _recommend(run_cli, tmp_path, consent_file)

    assert result.returncode == 0, result.stderr
    checkpoint = _read_state(tmp_path)["specialists_decision"]
    assert checkpoint["decision"] == "none"
    assert checkpoint["lifecycle_state"] == "terminal"
    assert checkpoint["reason_code"] == "profile-not-applicable"
    assert checkpoint["confirmation_resolved"] is True


def test_unconsented_confirmation_provider_remains_candidate(run_cli, tmp_path):
    _write_confirmation_registry(tmp_path)
    consent_file = tmp_path / "provider-consent.json"
    run_cli("init", "selection checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)

    result = _recommend(run_cli, tmp_path, consent_file)

    assert result.returncode == 0, result.stderr
    checkpoint = _read_state(tmp_path)["specialists_decision"]
    assert checkpoint["decision"] == "none"
    assert checkpoint["reason_code"] == "awaiting-confirmation"
    assert checkpoint["lifecycle_state"] == "candidate"


def test_decline_transitions_current_candidate_to_terminal_declined(run_cli, tmp_path):
    _write_confirmation_registry(tmp_path)
    consent_file = tmp_path / "provider-consent.json"
    run_cli("init", "selection checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)
    recommended = _recommend(run_cli, tmp_path, consent_file)
    assert recommended.returncode == 0, recommended.stderr
    selection_id = _read_state(tmp_path)["specialists_decision"]["selection_id"]

    result = run_cli(
        "specialists",
        "decline",
        "--selection-id",
        selection_id,
        "--reason",
        "core reviewers are sufficient for this mission",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    checkpoint = _read_state(tmp_path)["specialists_decision"]
    assert payload["specialists_decision"] == checkpoint
    assert checkpoint["selection_id"] == selection_id
    assert checkpoint["decision"] == "declined"
    assert checkpoint["reason_code"] == "orchestrator-declined"
    assert checkpoint["reason"] == "core reviewers are sufficient for this mission"
    assert checkpoint["lifecycle_state"] == "terminal"


def test_decline_rejects_stale_selection_id_without_mutation(run_cli, tmp_path):
    _write_confirmation_registry(tmp_path)
    consent_file = tmp_path / "provider-consent.json"
    run_cli("init", "selection checkpoint", "--complexity", "Standard", cwd=tmp_path, check=True)
    recommended = _recommend(run_cli, tmp_path, consent_file)
    assert recommended.returncode == 0, recommended.stderr
    state_file = tmp_path / ".mission-state" / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "specialists",
        "decline",
        "--selection-id",
        "sel_ffffffffffffffffffffffffffffffff",
        "--reason",
        "stale decision must not apply",
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "specialist-selection-id-mismatch" in result.stderr
    assert state_file.read_bytes() == before


def test_decline_executes_through_v5_repository(raw_run_cli, tmp_path):
    _write_confirmation_registry(tmp_path)
    consent_file = tmp_path / "provider-consent.json"
    raw_run_cli(
        "init", "selection checkpoint", "--complexity", "Standard",
        cwd=tmp_path, check=True,
    )
    recommended = raw_run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Update README React",
        "--installed-skills", "external-review-provider",
        "--consent-file", str(consent_file),
        "--record-state", "--json",
        cwd=tmp_path,
        env_extra={"MISSION_OPERATION_ID": "op-issue659-recommend"},
    )
    assert recommended.returncode == 0, recommended.stderr
    before = json.loads(raw_run_cli("get", cwd=tmp_path, check=True).stdout)
    selection_id = before["specialists_decision"]["selection_id"]

    result = raw_run_cli(
        "specialists", "decline",
        "--selection-id", selection_id,
        "--reason", "core reviewers are sufficient for this mission",
        "--json", cwd=tmp_path,
        env_extra={"MISSION_OPERATION_ID": "op-issue659-decline"},
    )

    assert result.returncode == 0, result.stderr
    checkpoint = json.loads(raw_run_cli("get", cwd=tmp_path, check=True).stdout)[
        "specialists_decision"
    ]
    assert checkpoint["selection_id"] == selection_id
    assert checkpoint["decision"] == "declined"
    assert checkpoint["lifecycle_state"] == "terminal"
