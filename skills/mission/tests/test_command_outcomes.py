"""#386: command outcome telemetry is bounded, safe, and observable."""

from __future__ import annotations

import json
import hashlib
import pytest
from command_outcomes import LIMIT, OutcomeStoreError, append_sidecar


def _review_bytes():
    return (json.dumps({
        "schema": "mission-review/1", "iteration": 1, "perspective": "quality",
        "scores": {"mission_achievement": 4.5, "accuracy": 4.5, "completeness": 4.5, "usability": 4.5},
        "findings": [],
    }) + "\n").encode("utf-8")


def test_stats_and_audit_count_state_and_sidecar_command_outcomes(state_dir, run_cli, tmp_path):
    review = tmp_path / "bad.json"
    review.write_text('{"schema":"wrong"}', encoding="utf-8")

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(review),
        "--event-id", "attempt-2", "--root-event-id", "root-1", "--attempt", "2",
        "--retry-of", "attempt-1", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome"] == {
        "event_id": "attempt-2", "root_event_id": "root-1", "attempt": 2,
        "retry_of": "attempt-1", "command": "review-import", "outcome_kind": "invalid-input",
    }
    stats = json.loads(run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent).stdout)
    assert stats["command_outcome_counts"] == {
        "ok": 0, "expected-gate": 0, "invalid-input": 1, "external": 0,
        "internal-error": 0, "unique_root_events": 1, "retry_count": 1,
        "invalid_records": 0, "corrupt_sidecars": 0,
    }


def test_corrupt_sidecar_is_never_silently_accepted_and_is_visible_in_stats(state_dir, run_cli):
    telemetry = state_dir / "telemetry" / "command-outcomes"
    telemetry.mkdir(parents=True)
    token = hashlib.sha256(b"test").hexdigest()[:16]
    (telemetry / f"{token}.json").write_text("not-json", encoding="utf-8")

    result = run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent)

    assert result.returncode == 0
    assert json.loads(result.stdout)["command_outcome_counts"]["corrupt_sidecars"] == 1


def test_command_provider_unavailable_is_an_external_outcome_producer(state_dir, run_cli):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["specialists_selected"] = [{
        "role": "evidence", "skill": "fixture-provider", "kind": "command",
        "command": "definitely-not-an-installed-command", "args": [], "source": "registry:$PROJECT",
    }]
    state["specialists_decision"] = {"policy": "auto"}
    state_file.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "specialists", "invoke-command", "--provider", "fixture-provider", "--iteration", "1",
        "--phase", "review", "--event-id", "provider-attempt", cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome_kind"] == "external"


def test_unexpected_provider_packet_error_is_internal_without_leaking_path(state_dir, run_cli, tmp_path):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["specialists_selected"] = [{
        "role": "evidence", "skill": "fixture-provider", "kind": "command",
        "command": "echo", "args": [], "source": "registry:$PROJECT",
    }]
    state["specialists_decision"] = {"policy": "auto"}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    missing = tmp_path / "not-present.json"

    result = run_cli(
        "specialists", "invoke-command", "--provider", "fixture-provider", "--iteration", "1",
        "--phase", "review", "--input-file", str(missing), "--event-id", "internal-attempt",
        cwd=state_dir.parent,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome_kind"] == "internal-error"
    assert str(missing) not in result.stdout


def test_phase_and_lease_gates_keep_state_bytes_and_emit_expected_gate_sidecars(state_dir, run_cli):
    state_file = state_dir / "sessions" / "test.json"
    before_phase = state_file.read_bytes()
    phase = run_cli(
        "advance", "--phase", "halted", "--json", "--event-id", "phase-gate", cwd=state_dir.parent,
    )
    assert phase.returncode == 2
    assert json.loads(phase.stdout)["outcome_kind"] == "expected-gate"
    assert state_file.read_bytes() == before_phase

    state = json.loads(before_phase)
    state.update({
        "owner_session_id": "another-session", "lease_id": "another-lease", "fencing_epoch": 1,
        "lease_expires_at": "2099-01-01T00:00:00Z",
    })
    state_file.write_text(json.dumps(state), encoding="utf-8")
    before_lease = state_file.read_bytes()
    lease = run_cli("set", "phase=executing", "--json", "--event-id", "lease-gate", cwd=state_dir.parent)
    assert lease.returncode == 2
    assert json.loads(lease.stdout)["outcome_kind"] == "expected-gate"
    assert state_file.read_bytes() == before_lease


@pytest.mark.parametrize(("option", "value"), [
    ("--event-id", "../escape"), ("--root-event-id", "/absolute"),
    ("--retry-of", "contains space"), ("--attempt", "0"),
])
def test_lineage_inputs_are_validated_before_review_state_write(state_dir, run_cli, tmp_path, option, value):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli("review-import", "--iteration", "1", "--input", str(source), option, value, cwd=state_dir.parent)

    assert result.returncode == 2
    assert json.loads(result.stdout)["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == before


def _outcome(index):
    return {"event_id": f"event-{index}", "root_event_id": "root", "attempt": index + 1,
            "retry_of": "prior" if index else None, "command": "fixture", "outcome_kind": "expected-gate"}


def test_sidecar_cap_and_atomic_publish_leave_no_temp_files(state_dir):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    for index in range(LIMIT + 2):
        record = _outcome(index)
        if index == 0:
            record.pop("retry_of")
        append_sidecar(state_dir, token, record)
    directory = state_dir / "telemetry" / "command-outcomes"
    sidecar = json.loads((directory / f"{token}.json").read_text(encoding="utf-8"))
    assert len(sidecar["records"]) == LIMIT
    assert not list(directory.glob(".*.tmp"))


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "oversize"])
def test_sidecar_hostile_files_fail_closed(mode, state_dir, tmp_path):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    sidecar = directory / f"{token}.json"
    if mode == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        sidecar.symlink_to(target)
    elif mode == "hardlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        sidecar.hardlink_to(target)
    else:
        sidecar.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(OutcomeStoreError):
        append_sidecar(state_dir, token, _outcome(1))
